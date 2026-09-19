import logging
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy

DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close", "last_evaluated_closed_bar_id", "super_trend_mode", "super_trend_trailing_stop"]

logger = logging.getLogger("DualTrackExit")

class DualTrackExitStrategy(IExitStrategy):
    """
    雙軌平倉策略 v4 — 結構性崩塌 (Structural Collapse)

    ZONE A: Inside Band (收盤在 KC 軌內)
        盤中：1.5 ATR 硬停損（唯一防線，無微型防禦）
        收盤：無，死抱等突破

    ZONE B: Super Trend (已收在 KC 軌外)
        盤中：1.5 ATR 硬停損 + 特例K(>=2.0ATR 反向) 即時逃生
        收盤：結構性崩塌 = 收盤回到軌道內 AND 收出反向實體(close <= prev_open)
    """
    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame, current_price: float, **kwargs) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None

        side = position.get("side", "LONG")
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

        if "defense_line" not in state:
            defense = (entry_price - 1.5 * atr) if side == "LONG" else (entry_price + 1.5 * atr)
            state["defense_line"]      = defense
            state["active_stop_price"] = defense
            state["last_locked_level"] = 0

        active_stop_price = state.get("active_stop_price", state["defense_line"])
        is_super_trend    = position.get("super_trend_mode", False)

        # ── 盤中保命層（ZONE A & B 共用）────────────────────────────
        # ① 1.5 ATR 硬停損
        if side == "LONG" and current_price <= active_stop_price:
            tag = "EXIT_PROFIT_PROTECT_HIT" if state["last_locked_level"] > 0 else "EXIT_HARD_STOP_1.5_ATR"
            logger.warning(f"[HARD STOP] {tag} @ {current_price:.6f}")
            return tag
        if side == "SHORT" and current_price >= active_stop_price:
            tag = "EXIT_PROFIT_PROTECT_HIT" if state["last_locked_level"] > 0 else "EXIT_HARD_STOP_1.5_ATR"
            logger.warning(f"[HARD STOP] {tag} @ {current_price:.6f}")
            return tag

        # ② ZONE B 限定：特例 K (>=2.0 ATR 反向) 盤中即時逃生
        if is_super_trend:
            curr_body = abs(float(curr["close"]) - float(curr["open"]))
            curr_open = float(curr["open"])
            if curr_body >= 2.0 * atr:
                if side == "LONG" and current_price < curr_open:
                    logger.warning(f"[FAST_EXIT] Special K Reversal LONG @ {current_price:.6f}")
                    return "[FAST_EXIT] Super Trend Special K Reversal"
                if side == "SHORT" and current_price > curr_open:
                    logger.warning(f"[FAST_EXIT] Special K Reversal SHORT @ {current_price:.6f}")
                    return "[FAST_EXIT] Super Trend Special K Reversal"

        # ── 收盤評估（每根 K 棒收盤後觸發一次）─────────────────────
        prev_timestamp  = float(prev_1.get("timestamp", prev_1.name))
        last_closed_bar = position.get("last_evaluated_closed_bar_id")

        if last_closed_bar is not None and prev_timestamp <= last_closed_bar:
            return None

        position["last_evaluated_closed_bar_id"] = prev_timestamp

        close_p       = float(prev_1["close"])
        prev_open_p   = float(prev_1["open"])
        kc_upper_prev = float(prev_1.get("kc_upper", float("inf")))
        kc_lower_prev = float(prev_1.get("kc_lower", 0.0))

        # ZONE 判定
        just_entered_super = False
        if side == "LONG" and not is_super_trend and close_p >= kc_upper_prev:
            position["super_trend_mode"] = True
            is_super_trend = True
            just_entered_super = True
            logger.info(f"[MODE->SUPER_TREND] LONG close={close_p:.6f} >= kc_upper={kc_upper_prev:.6f}")
        elif side == "SHORT" and not is_super_trend and close_p <= kc_lower_prev:
            position["super_trend_mode"] = True
            is_super_trend = True
            just_entered_super = True
            logger.info(f"[MODE->SUPER_TREND] SHORT close={close_p:.6f} <= kc_lower={kc_lower_prev:.6f}")

        # ZONE B：結構性崩塌觸發
        # 雙重確認：收盤回軌道內 AND 收出反向實體
        if is_super_trend and not just_entered_super:
            if side == "LONG":
                broke_back = close_p < kc_upper_prev
                has_reversal = close_p <= prev_open_p
                if broke_back and has_reversal:
                    logger.warning(
                        f"[STRUCTURAL_COLLAPSE] LONG close={close_p:.6f} < kc_upper={kc_upper_prev:.6f} "
                        f"AND close({close_p:.6f}) <= prev_open({prev_open_p:.6f})"
                    )
                    return "[STRUCTURAL_COLLAPSE_EXIT] Closed Inside Band + Reversal Body (LONG)"
            else:
                broke_back = close_p > kc_lower_prev
                has_reversal = close_p >= prev_open_p
                if broke_back and has_reversal:
                    logger.warning(
                        f"[STRUCTURAL_COLLAPSE] SHORT close={close_p:.6f} > kc_lower={kc_lower_prev:.6f} "
                        f"AND close({close_p:.6f}) >= prev_open({prev_open_p:.6f})"
                    )
                    return "[STRUCTURAL_COLLAPSE_EXIT] Closed Inside Band + Reversal Body (SHORT)"
            return None  # 還在軌外，死抱

        # ZONE A：通道內，無任何獲利了結，等突破
        logger.debug(f"[ZONE A] Holding inside band. close={close_p:.6f}")
        return None

    def handle_post_exit_cleanup(self, position: Dict[str, Any], exit_reason: str):
        symbol = position.get("symbol", "UNKNOWN")
        logger.info(f"[Post-Exit] {exit_reason} ({symbol})")
        for key in ("v10_phase_trailing", "last_evaluated_closed_bar_id",
                    "super_trend_mode", "super_trend_trailing_stop"):
            position.pop(key, None)
        if exit_reason and (exit_reason.startswith("EXIT_HARD_STOP") or
                            exit_reason.startswith("EXIT_CRASH_DEFENSE") or
                            exit_reason.startswith("EXIT_MOMENTUM_REVERSAL")):
            position["cooldown_mode"]     = "WAIT_FOR_STABLE_KC"
            position["cooldown_kc_count"] = 2
        else:
            position["cooldown_mode"] = "NONE"
        position["force_space_reevaluation"] = True
