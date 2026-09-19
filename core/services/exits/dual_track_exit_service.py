import logging
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy

DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close", "last_evaluated_closed_bar_id", "super_trend_mode", "super_trend_trailing_stop"]

logger = logging.getLogger("DualTrackExit")

class DualTrackExitStrategy(IExitStrategy):
    """
    雙軌平倉策略 v3 — 通道內/外雙重生存規則

    ┌─────────────────────────────────────────────────────────────┐
    │  ZONE A: Inside Band  (價格尚未收在 KC 軌外)               │
    │  目標：極度耐壓，只有「真正崩盤」才平倉                     │
    │  盤中允許:  ① 1.5 ATR 硬停損  ② 大實體崩盤 (>=1.5 ATR)   │
    │  收盤允許:  無（一律持倉等待突破）                           │
    ├─────────────────────────────────────────────────────────────┤
    │  ZONE B: Super Trend  (價格已收在 KC 軌外)                  │
    │  目標：護航到底，只有「收盤反穿前K極值」才平倉              │
    │  盤中允許:  ① 1.5 ATR 硬停損  ② 特例K(>=2.0 ATR) 即時逃生 │
    │  收盤允許:  收盤跌破前K最低點 / 收盤突破前K最高點           │
    └─────────────────────────────────────────────────────────────┘
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

        curr  = frame.iloc[-1]
        prev_1 = frame.iloc[-2]

        atr = float(prev_1.get("atr", 1.0))
        if atr <= 0:
            atr = entry_price * 0.01

        # ── 初始化硬停損防線 ──────────────────────────────────────────
        if "defense_line" not in state:
            v8_reason = position.get("v8_reason", position.get("reason", ""))
            defense = (entry_price - 1.5 * atr) if side == "LONG" else (entry_price + 1.5 * atr)
            state["defense_line"]      = defense
            state["active_stop_price"] = defense
            state["last_locked_level"] = 0

            if v8_reason and ("[SPECIAL_ENTRY] Extreme Impulse" in v8_reason):
                state["last_locked_level"] = 1
                state["active_stop_price"] = entry_price

        last_locked_level = state.get("last_locked_level", 0)
        active_stop_price = state.get("active_stop_price", state["defense_line"])
        is_super_trend = position.get("super_trend_mode", False)
        
        candle_body = abs(curr['close'] - curr['open'])

        # =====================================================================
        # 1. INTRADAY TICK EVALUATION (Hard Stop & Momentum Reversal Only)
        # =====================================================================
        
        # Intraday Check A: Crash Defense (Engulfing / Surge) - ALWAYS OVERRIDES
        if side == "LONG":
            is_engulfing = (current_price < curr['open']) and (curr['open'] >= prev_1['close']) and (current_price < prev_1['open'])
            is_crash = (current_price < curr['open']) and (candle_body > 1.5 * atr)
            if is_engulfing or is_crash:
                logger.warning(f"[EXIT_MOMENTUM_REVERSAL_DEFENSE] (LONG Crash/Engulfing)")
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE (LONG Crash/Engulfing)"
        elif side == "SHORT":
            is_engulfing = (current_price > curr['open']) and (curr['open'] <= prev_1['close']) and (current_price > prev_1['open'])
            is_surge = (current_price > curr['open']) and (candle_body > 1.5 * atr)
            if is_engulfing or is_surge:
                logger.warning(f"[EXIT_MOMENTUM_REVERSAL_DEFENSE] (SHORT Surge/Engulfing)")
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE (SHORT Surge/Engulfing)"

        # Intraday Check B: Base Hard Stop (Loss Protection Only)
        # The active_stop_price will only be stepped up at Bar Close
        if side == "LONG" and current_price <= active_stop_price:
            tag = "EXIT_PROFIT_PROTECT_HIT" if state["last_locked_level"] > 0 else "EXIT_HARD_STOP_1.5_ATR"
            return tag
        elif side == "SHORT" and current_price >= active_stop_price:
            tag = "EXIT_PROFIT_PROTECT_HIT" if state["last_locked_level"] > 0 else "EXIT_HARD_STOP_1.5_ATR"
            return tag

        # =====================================================================
        # 2. BAR CLOSE EVALUATION (Profit Taking, Super Trend, Fast Exit)
        # =====================================================================
        prev_timestamp = float(prev_1.get("timestamp", prev_1.name))
        last_closed_bar = position.get("last_evaluated_closed_bar_id")
        
        if last_closed_bar is None or prev_timestamp > last_closed_bar:
            position["last_evaluated_closed_bar_id"] = prev_timestamp
            
            close_p = float(prev_1['close'])
            kc_upper_prev = float(prev_1.get("kc_upper", float('inf')))
            kc_lower_prev = float(prev_1.get("kc_lower", 0.0))
            
            # Trend Context Detection
            slope_ma15 = float(prev_1.get("ma15_slope", 0))
            slope_middle = float(prev_1.get("kc_middle_slope", 0))
            is_strong_bear_trend = (slope_ma15 < -0.05 * atr) and (slope_middle < -0.05 * atr)
            is_strong_bull_trend = (slope_ma15 > 0.05 * atr) and (slope_middle > 0.05 * atr)
            
            if side == "LONG":
                unrealized_profit_atr = (close_p - entry_price) / atr
                has_trend_privilege = is_strong_bull_trend
            else:
                unrealized_profit_atr = (entry_price - close_p) / atr
                has_trend_privilege = is_strong_bear_trend

            # Check Super Trend Mode Activation (Close outside KC)
            if side == "LONG" and not is_super_trend:
                if close_p >= kc_upper_prev:
                    position["super_trend_mode"] = True
                    is_super_trend = True
                    position["super_trend_trailing_stop"] = float(prev_1['low'])
                    logger.info(f"[MODE_CHANGE] - Entered Super_Trend_Mode (LONG) for {position.get('symbol')}")
            elif side == "SHORT" and not is_super_trend:
                if close_p <= kc_lower_prev:
                    position["super_trend_mode"] = True
                    is_super_trend = True
                    position["super_trend_trailing_stop"] = float(prev_1['high'])
                    logger.info(f"[MODE_CHANGE] - Entered Super_Trend_Mode (SHORT) for {position.get('symbol')}")
            
            # Super Trend Check (Overrides Fast Exit and Step Trailing)
            if is_super_trend:
                if side == "LONG":
                    current_trailing = position.get("super_trend_trailing_stop", float('-inf'))
                    position["super_trend_trailing_stop"] = max(current_trailing, float(prev_1['low']))
                    if close_p < position["super_trend_trailing_stop"]:
                        return "[SUPER_TREND_EXIT] Bar Close Below Previous Low"
                else:
                    current_trailing = position.get("super_trend_trailing_stop", float('inf'))
                    position["super_trend_trailing_stop"] = min(current_trailing, float(prev_1['high']))
                    if close_p > position["super_trend_trailing_stop"]:
                        return "[SUPER_TREND_EXIT] Bar Close Above Previous High"
                        
            # If we have trend privilege, we skip all profit taking evaluations to ride the trend
            if has_trend_privilege:
                return None
                
            # If not in Super Trend and no Trend Privilege, evaluate Fast Exit & Step Trailing
            if not is_super_trend:
                is_special_k = abs(prev_1['close'] - prev_1['open']) >= (2.0 * atr)
                is_high_profit = unrealized_profit_atr >= 2.0
                
                # Fast Exit (Closed on/outside KC with Special K or High Profit)
                if is_special_k or is_high_profit:
                    if side == "LONG" and close_p >= kc_upper_prev:
                        reason = "Special K (>=2.0 ATR)" if is_special_k else "High Profit (>=2.0 ATR)"
                        return f"[FAST_EXIT] - Triggered by {reason}"
                    elif side == "SHORT" and close_p <= kc_lower_prev:
                        reason = "Special K (>=2.0 ATR)" if is_special_k else "High Profit (>=2.0 ATR)"
                        return f"[FAST_EXIT] - Triggered by {reason}"
                        
                # Step Trailing Lock
                step_size_atr = 1.5
                current_level = int(unrealized_profit_atr // step_size_atr)
                if current_level > last_locked_level:
                    state["last_locked_level"] = current_level
                    if side == "LONG":
                        new_stop = entry_price + ((current_level - 1) * step_size_atr * atr)
                        state["active_stop_price"] = max(active_stop_price, new_stop)
                    else:
                        new_stop = entry_price - ((current_level - 1) * step_size_atr * atr)
                        state["active_stop_price"] = min(active_stop_price, new_stop)
                    
                    # If step trailing lock is moved, evaluate immediately if we breached it on close
                    if side == "LONG" and close_p <= state["active_stop_price"]:
                        return "EXIT_PROFIT_PROTECT_HIT"
                    elif side == "SHORT" and close_p >= state["active_stop_price"]:
                        return "EXIT_PROFIT_PROTECT_HIT"
                        
        return None

    def handle_post_exit_cleanup(self, position: dict, exit_reason: str):
        import logging
        logger = logging.getLogger("DualTrackExit")
        symbol = position.get("symbol", "UNKNOWN")
        logger.info(f"[Post-Exit Cleanup] 啟動平倉後狀態清理，原因: {exit_reason} (幣種: {symbol})")
        
        if "v10_phase_trailing" in position:
            position.pop("v10_phase_trailing")
        if "last_evaluated_closed_bar_id" in position:
            position.pop("last_evaluated_closed_bar_id")
            
        if exit_reason and (exit_reason.startswith("EXIT_HARD_STOP") or exit_reason.startswith("EXIT_MOMENTUM_REVERSAL")):
            position["cooldown_mode"] = "WAIT_FOR_STABLE_KC"
            position["cooldown_kc_count"] = 2
        else:
            position["cooldown_mode"] = "NONE"
            
        position["force_space_reevaluation"] = True
