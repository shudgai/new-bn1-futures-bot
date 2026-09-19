import logging
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy

DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close",
                          "last_evaluated_closed_bar_id", "super_trend_mode", "super_trend_trailing_stop"]

logger = logging.getLogger("DualTrackExit")

# 統一 ATR 門檻常數
HARD_STOP_ATR        = 1.5   # 絕對保命線 (層級一)
SPECIAL_K_ATR        = 2.0   # 特例 K 反向實體 (層級二)
HIGH_PROFIT_ATR      = 2.0   # 盤中高利潤逃生門檻 (層級二)

class DualTrackExitStrategy(IExitStrategy):
    """
    雙軌平倉策略 v6 — 隔離式絕對防禦架構
    
    透過徹底清空狀態變數與微型過濾，本策略實行最純粹的三層防護：
    層級一 (盤中保命線): 1.5 ATR 硬停損。
    層級二 (盤中爆發逃生): 帳面利潤 >= 2.0 ATR 且遭遇單根 >= 2.0 ATR 狂暴反轉實體時緊急逃生。
    層級三 (收盤護航): 進入護航模式後，唯一的出場條件是收盤跌破 (多單) 或突破 (空單) 前一根 K 棒極值。
    """

    def initialize_position(self, position: Dict[str, Any], entry_price: float, atr: float) -> None:
        """
        強制狀態清空 (State Purge):
        確保新開倉的交易絕對不受舊交易殘留狀態干擾。
        """
        side = position.get("side", "LONG")
        defense_line = (entry_price - HARD_STOP_ATR * atr) if side == "LONG" else (entry_price + HARD_STOP_ATR * atr)
        
        if "v10_phase_trailing" not in position:
            position["v10_phase_trailing"] = {}
            
        state = position["v10_phase_trailing"]
        
        # 強制覆寫與歸零
        state["defense_line"]      = defense_line
        state["active_stop_price"] = defense_line
        state["last_locked_level"] = 0
        
        position["super_trend_mode"] = False
        position["super_trend_trailing_stop"] = None
        position["last_evaluated_closed_bar_id"] = None
        
        logger.info(f"[STATE_PURGE] Position initialized. Hard Stop: {defense_line:.6f}")

    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame,
                      current_price: float, **kwargs) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None

        side        = position.get("side", "LONG")
        entry_price = float(position.get("entry_price", 0.0))
        if entry_price <= 0:
            return None

        curr   = frame.iloc[-1]
        prev_1 = frame.iloc[-2]

        atr = float(prev_1.get("atr", 1.0))
        if atr <= 0:
            atr = entry_price * 0.01

        # 若尚未初始化，則進行初始化
        if "v10_phase_trailing" not in position or "active_stop_price" not in position.get("v10_phase_trailing", {}):
            self.initialize_position(position, entry_price, atr)

        state = position["v10_phase_trailing"]
        is_super = position.get("super_trend_mode", False)
        
        # 取得當前適用的保命線
        active_stop = state.get("active_stop_price", state.get("defense_line", entry_price))
        
        # ══════════════════════════════════════════════════════════════
        # 層級一：絕對保命 (1.5 ATR 硬停損)
        # 不論是否在護航模式，皆以最初設定的硬停損為底線
        # ══════════════════════════════════════════════════════════════
        if side == "LONG" and current_price <= active_stop:
            logger.warning(f"[EXIT_HARD_STOP] LONG hit 1.5 ATR stop @ {current_price:.6f}")
            return "EXIT_HARD_STOP_1.5_ATR"
        if side == "SHORT" and current_price >= active_stop:
            logger.warning(f"[EXIT_HARD_STOP] SHORT hit 1.5 ATR stop @ {current_price:.6f}")
            return "EXIT_HARD_STOP_1.5_ATR"

        # 盤中資料計算
        curr_open  = float(curr["open"])
        curr_close = float(curr.get("close", current_price))
        curr_body  = abs(curr_close - curr_open)

        # 計算當前帳面利潤 ATR (以判斷是否符合爆發逃生條件)
        if side == "LONG":
            unrealized_profit_atr = (current_price - entry_price) / atr
        else:
            unrealized_profit_atr = (entry_price - current_price) / atr

        # ══════════════════════════════════════════════════════════════
        # 層級二：盤中爆發逃生 (高利潤 + 狂暴反轉)
        # ══════════════════════════════════════════════════════════════
        if unrealized_profit_atr >= HIGH_PROFIT_ATR and curr_body >= SPECIAL_K_ATR * atr:
            if side == "LONG" and current_price < curr_open:
                logger.warning(f"[EXIT_CRASH_ESCAPE] LONG Special K Escape (Profit: {unrealized_profit_atr:.2f} ATR) @ {current_price:.6f}")
                return "EXIT_CRASH_ESCAPE"
            if side == "SHORT" and current_price > curr_open:
                logger.warning(f"[EXIT_CRASH_ESCAPE] SHORT Special K Escape (Profit: {unrealized_profit_atr:.2f} ATR) @ {current_price:.6f}")
                return "EXIT_CRASH_ESCAPE"

        # ══════════════════════════════════════════════════════════════
        # 收盤評估與層級三護航邏輯
        # ══════════════════════════════════════════════════════════════
        prev_ts         = float(prev_1.get("timestamp", prev_1.name))
        last_closed_bar = position.get("last_evaluated_closed_bar_id")

        if last_closed_bar is not None and prev_ts <= last_closed_bar:
            # 尚未產生新的收盤 K 棒，不再進行收盤與護航更新
            return None

        # 標記當前這根 K 棒為已評估
        position["last_evaluated_closed_bar_id"] = prev_ts

        close_p       = float(prev_1["close"])
        kc_upper_prev = float(prev_1.get("kc_upper", float("inf")))
        kc_lower_prev = float(prev_1.get("kc_lower", 0.0))
        prev_low      = float(prev_1["low"])
        prev_high     = float(prev_1["high"])

        # 檢查是否應進入護航模式 (ZONE B)
        just_entered_super_trend = False
        if side == "LONG" and not is_super and close_p >= kc_upper_prev:
            position["super_trend_mode"]          = True
            position["super_trend_trailing_stop"] = prev_low
            is_super = True
            just_entered_super_trend = True
            logger.info(f"[MODE→SUPER_TREND] LONG 收盤突破 KC 上軌 close={close_p:.6f}, 初始護航線={prev_low:.6f}")

        elif side == "SHORT" and not is_super and close_p <= kc_lower_prev:
            position["super_trend_mode"]          = True
            position["super_trend_trailing_stop"] = prev_high
            is_super = True
            just_entered_super_trend = True
            logger.info(f"[MODE→SUPER_TREND] SHORT 收盤跌破 KC 下軌 close={close_p:.6f}, 初始護航線={prev_high:.6f}")

        # 層級三：收盤護航出場判斷
        if is_super and not just_entered_super_trend:
            current_trail = position.get("super_trend_trailing_stop")
            if current_trail is not None:
                if side == "LONG" and close_p < current_trail:
                    logger.warning(f"[ZONE_B_EXIT] LONG Bar Close < Trailing Stop {current_trail:.6f}")
                    return "EXIT_SUPER_TREND_BROKEN"
                elif side == "SHORT" and close_p > current_trail:
                    logger.warning(f"[ZONE_B_EXIT] SHORT Bar Close > Trailing Stop {current_trail:.6f}")
                    return "EXIT_SUPER_TREND_BROKEN"

            # 推進護航線：每一根 K 棒收盤後，更新防線為該根 K 棒的極值，只進不退
            if side == "LONG":
                new_trail = max(current_trail if current_trail else float("-inf"), prev_low)
                if new_trail > (current_trail if current_trail else float("-inf")):
                    position["super_trend_trailing_stop"] = new_trail
                    logger.info(f"[TRAILING_GUARD_UPDATE] LONG 護航線上移至 {new_trail:.6f}")
            else:
                new_trail = min(current_trail if current_trail else float("inf"), prev_high)
                if new_trail < (current_trail if current_trail else float("inf")):
                    position["super_trend_trailing_stop"] = new_trail
                    logger.info(f"[TRAILING_GUARD_UPDATE] SHORT 護航線下移至 {new_trail:.6f}")

        return None

    def handle_post_exit_cleanup(self, position: Dict[str, Any], exit_reason: str):
        symbol = position.get("symbol", "UNKNOWN")
        logger.info(f"[Post-Exit] {exit_reason} ({symbol})")
        for key in ("v10_phase_trailing", "last_evaluated_closed_bar_id",
                    "super_trend_mode", "super_trend_trailing_stop"):
            position.pop(key, None)
        
        # 統一處理所有出場後的冷卻邏輯
        if exit_reason and (exit_reason.startswith("EXIT_HARD_STOP") or exit_reason.startswith("EXIT_CRASH_ESCAPE")):
            position["cooldown_mode"]     = "WAIT_FOR_STABLE_KC"
            position["cooldown_kc_count"] = 2
        else:
            position["cooldown_mode"] = "NONE"
            
        position["force_space_reevaluation"] = True
