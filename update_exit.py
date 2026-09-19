with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write('''import logging
from typing import Dict, Any, Optional
import pandas as pd
from core.services.exits import IExitStrategy

logger = logging.getLogger("DualTrackExit")

class DualTrackExitStrategy(IExitStrategy):
    """
    A unified exit strategy incorporating:
    - Intraday Hard Stop (1.5 ATR)
    - Intraday Step Trailing Lock (1.5 ATR steps)
    - Intraday Fast Exit (Touch KC & (Profit >= 2.0 ATR or Special K))
    - Bar Close Extreme Reversal Defense (Crash/Engulfing)
    - Bar Close Super Trend Mode (Candle Low/High Trailing)
    """

    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame, current_price: float) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None

        side = position.get("side")
        entry_price = float(position.get("entry_price") or 0)
        
        atr = position.get("entry_atr")
        if not atr:
            atr = float(frame.iloc[-1].get("atr", 0))
            if atr <= 0 and len(frame) >= 2:
                atr = float(frame.iloc[-2].get("atr", 0))
        if atr <= 0:
            return None
            
        state = position.setdefault("v10_phase_trailing", {})
        
        # Initialize hard stop
        if "defense_line" not in state:
            v8_reason = position.get("v8_reason", position.get("reason", ""))
            if side == "LONG":
                defense = entry_price - 1.5 * atr
            else:
                defense = entry_price + 1.5 * atr
                
            state["defense_line"] = defense
            state["active_stop_price"] = defense
            state["last_locked_level"] = 0
            
            if v8_reason and ("[SPECIAL_ENTRY] Extreme Impulse" in v8_reason):
                state["last_locked_level"] = 1
                state["active_stop_price"] = entry_price

        # State vars
        last_locked_level = state.get("last_locked_level", 0)
        active_stop_price = state.get("active_stop_price", state["defense_line"])
        is_super_trend = position.get("super_trend_mode", False)

        curr = frame.iloc[-1]
        prev_1 = frame.iloc[-2]
        prev_2 = frame.iloc[-3]
        
        kc_upper = float(curr.get("kc_upper", float('inf')))
        kc_lower = float(curr.get("kc_lower", 0.0))
        
        candle_body = abs(curr['close'] - curr['open'])
        is_special_k = candle_body >= (2.0 * atr)

        if side == "LONG":
            unrealized_profit_atr = (current_price - entry_price) / atr
        else:
            unrealized_profit_atr = (entry_price - current_price) / atr

        # =====================================================================
        # 1. INTRADAY TICK EVALUATION (Fast Exits & Standard Step Trailing)
        # =====================================================================
        
        # Intraday Check A: Fast Exit Waiver (Special K or High Profit >= 2.0 ATR touching KC)
        is_high_profit = unrealized_profit_atr >= 2.0
        if is_special_k or is_high_profit:
            if side == "LONG" and current_price >= kc_upper:
                reason = "Special K (>=2.0 ATR)" if is_special_k else "High Profit (>=2.0 ATR)"
                logger.warning(f"[FAST_EXIT] - Triggered by {reason} (LONG)")
                return f"[FAST_EXIT] - Triggered by {reason}"
            elif side == "SHORT" and current_price <= kc_lower:
                reason = "Special K (>=2.0 ATR)" if is_special_k else "High Profit (>=2.0 ATR)"
                logger.warning(f"[FAST_EXIT] - Triggered by {reason} (SHORT)")
                return f"[FAST_EXIT] - Triggered by {reason}"
                
        # Intraday Check B: Base Hard Stop & Step Trailing Lock 
        # (Disabled if Super Trend is active, as Super Trend uses Close-based evaluation)
        if not is_super_trend:
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
                active_stop_price = state["active_stop_price"]
                
            # Intraday Hard Stop Trigger
            if side == "LONG" and current_price <= active_stop_price:
                tag = "EXIT_PROFIT_PROTECT_HIT" if state["last_locked_level"] > 0 else "EXIT_HARD_STOP_1.5_ATR"
                return tag
            elif side == "SHORT" and current_price >= active_stop_price:
                tag = "EXIT_PROFIT_PROTECT_HIT" if state["last_locked_level"] > 0 else "EXIT_HARD_STOP_1.5_ATR"
                return tag

        # =====================================================================
        # 2. BAR CLOSE EVALUATION (Super Trend & Crash Defense)
        # =====================================================================
        prev_timestamp = float(prev_1.get("timestamp", prev_1.name))
        last_closed_bar = position.get("last_evaluated_closed_bar_id")
        
        if last_closed_bar is None or prev_timestamp > last_closed_bar:
            position["last_evaluated_closed_bar_id"] = prev_timestamp
            close_p = float(prev_1['close'])
            prev_body = abs(prev_1['close'] - prev_1['open'])
            
            # Crash Defense (Engulfing / Surge)
            if side == "LONG":
                is_engulfing = (close_p < prev_1['open']) and (prev_1['open'] >= prev_2['close']) and (close_p < prev_2['open'])
                is_crash = (close_p < prev_1['open']) and (prev_body > 1.5 * atr)
                if is_engulfing or is_crash:
                    return "EXIT_MOMENTUM_REVERSAL_DEFENSE (LONG Crash/Engulfing)"
            elif side == "SHORT":
                is_engulfing = (close_p > prev_1['open']) and (prev_1['open'] <= prev_2['close']) and (close_p > prev_2['open'])
                is_surge = (close_p > prev_1['open']) and (prev_body > 1.5 * atr)
                if is_engulfing or is_surge:
                    return "EXIT_MOMENTUM_REVERSAL_DEFENSE (SHORT Surge/Engulfing)"
                    
            # Super Trend Check
            kc_upper_prev = float(prev_1.get("kc_upper", float('inf')))
            kc_lower_prev = float(prev_1.get("kc_lower", 0.0))
            
            if side == "LONG":
                if not is_super_trend:
                    if close_p >= kc_upper_prev:
                        position["super_trend_mode"] = True
                        is_super_trend = True
                        position["super_trend_trailing_stop"] = float(prev_1['low'])
                        logger.info(f"[MODE_CHANGE] - Entered Super_Trend_Mode (LONG) for {position.get('symbol')}")
                if is_super_trend:
                    current_trailing = position.get("super_trend_trailing_stop", float('-inf'))
                    position["super_trend_trailing_stop"] = max(current_trailing, float(prev_1['low']))
                    
                    if close_p < position["super_trend_trailing_stop"]:
                        return "[SUPER_TREND_EXIT] Bar Close Below Previous Low"
            else:
                if not is_super_trend:
                    if close_p <= kc_lower_prev:
                        position["super_trend_mode"] = True
                        is_super_trend = True
                        position["super_trend_trailing_stop"] = float(prev_1['high'])
                        logger.info(f"[MODE_CHANGE] - Entered Super_Trend_Mode (SHORT) for {position.get('symbol')}")
                if is_super_trend:
                    current_trailing = position.get("super_trend_trailing_stop", float('inf'))
                    position["super_trend_trailing_stop"] = min(current_trailing, float(prev_1['high']))
                    
                    if close_p > position["super_trend_trailing_stop"]:
                        return "[SUPER_TREND_EXIT] Bar Close Above Previous High"

        return None

    def handle_post_exit_cleanup(self, position: Dict[str, Any], exit_reason: str):
        symbol = position.get("symbol", "UNKNOWN")
        logger.info(f"[Post-Exit Cleanup] 啟動平倉後狀態清理，原因: {exit_reason} (幣種: {symbol})")
        
        if "v10_phase_trailing" in position:
            position.pop("v10_phase_trailing")
        if "last_evaluated_closed_bar_id" in position:
            position.pop("last_evaluated_closed_bar_id")
            
        if exit_reason and exit_reason.startswith("EXIT_HARD_STOP") or exit_reason.startswith("EXIT_MOMENTUM_REVERSAL"):
            position["cooldown_mode"] = "WAIT_FOR_STABLE_KC"
            position["cooldown_kc_count"] = 2
        else:
            position["cooldown_mode"] = "NONE"
            
        position["force_space_reevaluation"] = True
''')
