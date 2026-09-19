import logging
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy

DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close",
                          "last_evaluated_closed_bar_id", "super_trend_mode", "super_trend_trailing_stop"]

logger = logging.getLogger("DualTrackExit")

# 統一 ATR 門檻常數
HARD_STOP_ATR        = 1.5   # 絕對保命線
CRASH_BODY_ATR       = 2.0   # 大實體崩盤防禦
SPECIAL_K_ATR        = 2.0   # 特例 K 盤中逃生
HIGH_PROFIT_ATR      = 2.0   # 高利潤盤中逃生


class DualTrackExitStrategy(IExitStrategy):
    """
    雙軌平倉策略 v5 — 全域對稱生存架構

    ┌─────────────────────────────────────────────────────────────────────┐
    │ 第一層：底線邏輯（任何 Zone 均有效）                               │
    │   盤中 → 1.5 ATR 硬停損                                           │
    ├─────────────────────────────────────────────────────────────────────┤
    │ ZONE A: Inside Band（收盤在 KC 軌內）                              │
    │   心態：無視震盪，死抱等突破                                       │
    │   盤中 → ① 1.5 ATR 硬停損                                        │
    │          ② 大實體崩盤（實體 >=2.0 ATR 且反向穿越前根開盤價）     │
    │   收盤 → ❌ 什麼都不做                                            │
    ├─────────────────────────────────────────────────────────────────────┤
    │ ZONE B: Super Trend（已收在 KC 軌外）                              │
    │   心態：死抱波峰，直到結構瓦解                                     │
    │   盤中 → ① 1.5 ATR 硬停損                                        │
    │          ② 特例 K（>=2.0 ATR 反向體）即時逃生                    │
    │          ③ 高利潤（>=2.0 ATR 帳面盈利）觸及對側 KC 軌即時逃生   │
    │   收盤 → 收盤跌破前K最低點 (LONG) / 突破前K最高點 (SHORT)       │
    └─────────────────────────────────────────────────────────────────────┘
    """

    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame,
                      current_price: float, **kwargs) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None

        side        = position.get("side", "LONG")
        entry_price = float(position.get("entry_price", 0.0))
        if entry_price <= 0:
            return None

        if "v10_phase_trailing" not in position:
            position["v10_phase_trailing"] = {}
        state = position["v10_phase_trailing"]

        curr   = frame.iloc[-1]
        prev_1 = frame.iloc[-2]

        atr = float(prev_1.get("atr", 1.0))
        if atr <= 0:
            atr = entry_price * 0.01

        # ── 初始化硬停損防線 ─────────────────────────────────────────
        if "defense_line" not in state:
            defense = (entry_price - HARD_STOP_ATR * atr) if side == "LONG"                       else (entry_price + HARD_STOP_ATR * atr)
            state["defense_line"]      = defense
            state["active_stop_price"] = defense
            state["last_locked_level"] = 0

        active_stop = state.get("active_stop_price", state["defense_line"])
        is_super    = position.get("super_trend_mode", False)

        # ── ZONE A 盤中階梯鎖利更新（Zone B 護航模式完全忽略盤中更新） ──
        if not is_super:
            unrealized_profit_atr = (current_price - entry_price) / atr if side == "LONG" else (entry_price - current_price) / atr
            if unrealized_profit_atr >= 1.5:
                locked_level = int(unrealized_profit_atr / 1.5)
                if locked_level > state.get("last_locked_level", 0):
                    step_atr = (locked_level * 1.5) - 1.0
                    new_active_stop = entry_price + step_atr * atr if side == "LONG" else entry_price - step_atr * atr
                    state["last_locked_level"] = locked_level
                    if side == "LONG":
                        state["active_stop_price"] = max(state.get("active_stop_price", float("-inf")), new_active_stop)
                    else:
                        state["active_stop_price"] = min(state.get("active_stop_price", float("inf")), new_active_stop)
                    logger.info(f"[ZONE_A_STEP_LOCK] Level {locked_level}, new stop: {state['active_stop_price']:.6f}")
                    
            active_stop = state.get("active_stop_price", state["defense_line"])

        # 盤中即時資料
        curr_open  = float(curr["open"])
        curr_close = float(curr.get("close", current_price))
        curr_body  = abs(curr_close - curr_open)
        prev_open  = float(prev_1["open"])

        # 第一層：絕對保命（ZONE A & B 共用，Zone B 只會使用凍結的保命線，無盤中鎖利更新）
        # ══════════════════════════════════════════════════════════════
        # 讓前端介面能讀取到防線
        position["stop_price"] = position.get("super_trend_trailing_stop") if is_super else active_stop
        if side == "LONG" and current_price <= active_stop:
            tag = "EXIT_PROFIT_PROTECT_HIT" if state["last_locked_level"] > 0 else "EXIT_HARD_STOP_1.5_ATR"
            logger.warning(f"[ZONE_A_EXIT][HARD_STOP] {tag} @ {current_price:.6f}")
            return tag
        if side == "SHORT" and current_price >= active_stop:
            tag = "EXIT_PROFIT_PROTECT_HIT" if state["last_locked_level"] > 0 else "EXIT_HARD_STOP_1.5_ATR"
            logger.warning(f"[ZONE_A_EXIT][HARD_STOP] {tag} @ {current_price:.6f}")
            return tag

        # ══════════════════════════════════════════════════════════════
        # ZONE A：通道內大實體崩盤防禦（>=2.0 ATR 且穿越前根開盤）
        # ══════════════════════════════════════════════════════════════
        if not is_super:
            if side == "LONG":
                if curr_body >= CRASH_BODY_ATR * atr and current_price < curr_open and current_price < prev_open:
                    logger.warning(f"[ZONE_A_EXIT][CRASH] Big Body Crash LONG body={curr_body/atr:.2f}ATR @ {current_price:.6f}")
                    return "EXIT_CRASH_DEFENSE (LONG BigBody>=2.0ATR < PrevOpen)"
            else:
                if curr_body >= CRASH_BODY_ATR * atr and current_price > curr_open and current_price > prev_open:
                    logger.warning(f"[ZONE_A_EXIT][CRASH] Big Body Surge SHORT body={curr_body/atr:.2f}ATR @ {current_price:.6f}")
                    return "EXIT_CRASH_DEFENSE (SHORT BigBody>=2.0ATR > PrevOpen)"

        # ══════════════════════════════════════════════════════════════
        # ZONE B 盤中：特例 K 與高利潤快速逃生
        # ══════════════════════════════════════════════════════════════
        if is_super:
            kc_upper_curr = float(curr.get("kc_upper", float("inf")))
            kc_lower_curr = float(curr.get("kc_lower", 0.0))

            # ② 特例 K（>=2.0 ATR 反向）
            if curr_body >= SPECIAL_K_ATR * atr:
                if side == "LONG" and current_price < curr_open:
                    logger.warning(f"[ZONE_B_EXIT][SPECIAL_K] Reversal LONG body={curr_body/atr:.2f}ATR @ {current_price:.6f}")
                    return "[ZONE_B_EXIT] Special K Reversal LONG"
                if side == "SHORT" and current_price > curr_open:
                    logger.warning(f"[ZONE_B_EXIT][SPECIAL_K] Reversal SHORT body={curr_body/atr:.2f}ATR @ {current_price:.6f}")
                    return "[ZONE_B_EXIT] Special K Reversal SHORT"

            # ③ 高利潤觸及軌道（帳面 >=2.0 ATR），初根K棒不平倉（給予呼吸空間）
            is_initial_bar = (position.get("last_evaluated_closed_bar_id") is None)
            
            if not is_initial_bar:
                if side == "LONG":
                    unrealized_atr = (current_price - entry_price) / atr
                    if unrealized_atr >= HIGH_PROFIT_ATR and current_price <= kc_upper_curr:
                        logger.warning(f"[ZONE_B_EXIT][HIGH_PROFIT] LONG profit={unrealized_atr:.2f}ATR touching KC_upper @ {current_price:.6f}")
                        return "[ZONE_B_EXIT] High Profit (>=2.0ATR) Touching KC LONG"
                else:
                    unrealized_atr = (entry_price - current_price) / atr
                    if unrealized_atr >= HIGH_PROFIT_ATR and current_price >= kc_lower_curr:
                        logger.warning(f"[ZONE_B_EXIT][HIGH_PROFIT] SHORT profit={unrealized_atr:.2f}ATR touching KC_lower @ {current_price:.6f}")
                        return "[ZONE_B_EXIT] High Profit (>=2.0ATR) Touching KC SHORT"

        # ══════════════════════════════════════════════════════════════
        # 收盤評估（每根 K 棒收盤後觸發一次）
        # ══════════════════════════════════════════════════════════════
        prev_ts         = float(prev_1.get("timestamp", prev_1.name))
        last_closed_bar = position.get("last_evaluated_closed_bar_id")

        if last_closed_bar is not None and prev_ts <= last_closed_bar:
            return None

        position["last_evaluated_closed_bar_id"] = prev_ts

        close_p       = float(prev_1["close"])
        kc_upper_prev = float(prev_1.get("kc_upper", float("inf")))
        kc_lower_prev = float(prev_1.get("kc_lower", 0.0))
        prev_low      = float(prev_1["low"])
        prev_high     = float(prev_1["high"])

        # ── ZONE 判定 ─────────────────────────────────────────────────
        just_entered = False
        if side == "LONG" and not is_super and close_p >= kc_upper_prev:
            position["super_trend_mode"]          = True
            position["super_trend_trailing_stop"] = prev_low
            is_super     = True
            just_entered = True
            logger.info(f"[MODE→ZONE_B] LONG 突破 KC 上軌 close={close_p:.6f}  trailing={prev_low:.6f}")

        elif side == "SHORT" and not is_super and close_p <= kc_lower_prev:
            position["super_trend_mode"]          = True
            position["super_trend_trailing_stop"] = prev_high
            is_super     = True
            just_entered = True
            logger.info(f"[MODE→ZONE_B] SHORT 跌破 KC 下軌 close={close_p:.6f}  trailing={prev_high:.6f}")

        # ══════════════════════════════════════════════════════════════
        # ZONE B 收盤：階梯護航（Step-Wise Guard）= Max(階梯鎖利, 前K極值)
        # ══════════════════════════════════════════════════════════════
        if is_super and not just_entered:
            unrealized_profit_atr = (close_p - entry_price) / atr if side == "LONG" else (entry_price - close_p) / atr
            
            # A線：計算階梯鎖利防線 (每進展 1.5 ATR 推進防線，保留 1.0 ATR 回吐空間)
            locked_level = int(unrealized_profit_atr / 1.5)
            step_atr = max(0.0, (locked_level * 1.5) - 1.0)
            
            if side == "LONG":
                step_line = entry_price + step_atr * atr
                guard_line = prev_low
                new_trail = max(step_line, guard_line)
                
                current_trail = position.get("super_trend_trailing_stop", float("-inf"))
                if new_trail > current_trail:
                    position["super_trend_trailing_stop"] = new_trail
                    if new_trail == step_line and new_trail != guard_line:
                        logger.info(f"[STEP_LOCK_UPDATE] LONG trailing→{new_trail:.6f} (Step ATR: {step_atr:.2f})")
                    else:
                        logger.info(f"[TRAILING_GUARD_UPDATE] LONG trailing→{new_trail:.6f} (Prev Low)")
                
                if close_p < position["super_trend_trailing_stop"]:
                    logger.warning(f"[ZONE_B_EXIT][SUPER_TREND] LONG Bar Close < Trailing Stop {position['super_trend_trailing_stop']:.6f}")
                    return "[ZONE_B_EXIT] Super Trend — Bar Close Below Trailing Stop (LONG)"
            else:
                step_line = entry_price - step_atr * atr
                guard_line = prev_high
                new_trail = min(step_line, guard_line)
                
                current_trail = position.get("super_trend_trailing_stop", float("inf"))
                if new_trail < current_trail:
                    position["super_trend_trailing_stop"] = new_trail
                    if new_trail == step_line and new_trail != guard_line:
                        logger.info(f"[STEP_LOCK_UPDATE] SHORT trailing→{new_trail:.6f} (Step ATR: {step_atr:.2f})")
                    else:
                        logger.info(f"[TRAILING_GUARD_UPDATE] SHORT trailing→{new_trail:.6f} (Prev High)")
                
                if close_p > position["super_trend_trailing_stop"]:
                    logger.warning(f"[ZONE_B_EXIT][SUPER_TREND] SHORT Bar Close > Trailing Stop {position['super_trend_trailing_stop']:.6f}")
                    return "[ZONE_B_EXIT] Super Trend — Bar Close Above Trailing Stop (SHORT)"
            return None  # 還在護航中，不平倉

        # ZONE A：通道內，收盤無任何出場條件
        logger.debug(f"[ZONE_A] Inside Band — holding. close={close_p:.6f}")
        return None

    def handle_post_exit_cleanup(self, position: Dict[str, Any], exit_reason: str):
        symbol = position.get("symbol", "UNKNOWN")
        logger.info(f"[Post-Exit] {exit_reason} ({symbol})")
        for key in ("v10_phase_trailing", "last_evaluated_closed_bar_id",
                    "super_trend_mode", "super_trend_trailing_stop"):
            position.pop(key, None)
        if exit_reason and (exit_reason.startswith("EXIT_HARD_STOP") or
                            exit_reason.startswith("EXIT_CRASH_DEFENSE")):
            position["cooldown_mode"]     = "WAIT_FOR_STABLE_KC"
            position["cooldown_kc_count"] = 2
        else:
            position["cooldown_mode"] = "NONE"
        position["force_space_reevaluation"] = True
