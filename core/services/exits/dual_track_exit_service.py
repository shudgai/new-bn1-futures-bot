import pandas as pd
from typing import Dict, Any, Optional
import logging
import math

logger = logging.getLogger(__name__)

# V10 狀態追蹤鍵列，持久化結構追蹤狀態
DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close", "last_evaluated_closed_bar_id"]

class DualTrackExitStrategy:
    def __init__(self, fee: float = 0.0004, slippage: float = 0.0005):
        self.fee = fee
        self.slippage = slippage

    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame, price: float, velocity_drop_ratio: float = 0.0, **kwargs) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        if not side:
            return None

        # 如果機器人正在開倉中，暫停所有平倉判定！
        if position.get("is_opening") is True or position.get("orders_pending") is True:
            return None

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]
        
        atr = position.get("entry_atr")
        if not atr:
            atr = float(curr.get("atr", 0))
            if atr <= 0:
                atr = float(prev.get("atr", 0))
                
        if atr <= 0:
            return None
            
        entry_price = float(position.get("entry_price", 0))
        if entry_price <= 0:
            return None

        # INTRADAY CHECKS (FAST EXITS)
        # 1. Hard Stop (1.5 ATR from entry)
        if side == "LONG" and price <= entry_price - 1.5 * atr:
            logger.warning(f"[Hard Stop] {position.get('symbol')} hit -1.5 ATR defense line.")
            return "EXIT_1.5_ATR_DEFENSE"
        elif side == "SHORT" and price >= entry_price + 1.5 * atr:
            logger.warning(f"[Hard Stop] {position.get('symbol')} hit -1.5 ATR defense line.")
            return "EXIT_1.5_ATR_DEFENSE"

        # 2. Emergency Escape (Momentum Reversal - Intraday)
        curr_open = float(curr["open"])
        curr_close = price
        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        curr_body = abs(curr_close - curr_open)
        
        is_curr_red = curr_close < curr_open
        is_curr_green = curr_close > curr_open
        
        if side == "LONG":
            engulfing = is_curr_red and (curr_open >= prev_close) and (curr_close < prev_open)
            crash = is_curr_red and (curr_body > 1.5 * atr)
            if engulfing or crash:
                logger.warning(f"[Emergency Escape] {position.get('symbol')} 觸發極速反噬防禦 (LONG)！市價平倉！")
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE"
        elif side == "SHORT":
            engulfing = is_curr_green and (curr_open <= prev_close) and (curr_close > prev_open)
            surge = is_curr_green and (curr_body > 1.5 * atr)
            if engulfing or surge:
                logger.warning(f"[Emergency Escape] {position.get('symbol')} 觸發極速反噬防禦 (SHORT)！市價平倉！")
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE"

        # 2.5 Counter-Trend Escape (逆勢單極速逃生機制)
        ma15_prev1 = float(prev.get('ma15', 0))
        ma15_prev2 = float(frame.iloc[-3].get('ma15', 0)) if len(frame) >= 3 else ma15_prev1
        slope_ma15 = ma15_prev1 - ma15_prev2
        
        is_counter_trend = False
        if side == "LONG" and slope_ma15 < 0:
            is_counter_trend = True
        elif side == "SHORT" and slope_ma15 > 0:
            is_counter_trend = True
            
        if is_counter_trend:
            ma3_curr = float(curr.get('ma3', 0))
            ma3_prev = float(prev.get('ma3', 0))
            slope_ma3 = ma3_curr - ma3_prev
            
            if side == "LONG" and is_curr_red and slope_ma3 < 0:
                logger.warning(f"[Emergency Escape] {position.get('symbol')} 逆勢單遭遇反向K棒且MA3下彎 (LONG)！保本逃命！")
                return "[FAST_EXIT] Counter-Trend Escape"
            elif side == "SHORT" and is_curr_green and slope_ma3 > 0:
                logger.warning(f"[Emergency Escape] {position.get('symbol')} 逆勢單遭遇反向K棒且MA3上彎 (SHORT)！保本逃命！")
                return "[FAST_EXIT] Counter-Trend Escape"

        # 3. Profit Waiver (High Profit Target)
        unrealized_profit = (price - entry_price) if side == "LONG" else (entry_price - price)
        kc_upper = float(curr.get("kc_upper", float('inf')))
        kc_lower = float(curr.get("kc_lower", 0.0))
        
        if unrealized_profit >= 1.5 * atr:
            if (side == "LONG" and price >= kc_upper) or (side == "SHORT" and price <= kc_lower):
                logger.warning(f"[FAST_EXIT] High Profit Target (>= 1.5 ATR) on {position.get('symbol')}! Taking profit.")
                return "[FAST_EXIT] High Profit Target"
                
        # 4. Special K Volatility
        if curr_body >= 2.0 * atr:
            if (side == "LONG" and price >= kc_upper) or (side == "SHORT" and price <= kc_lower):
                logger.warning(f"[FAST_EXIT] Special K (>= 2.0 ATR Body) on {position.get('symbol')}! Taking profit.")
                return "[FAST_EXIT] Special K"

        # END OF BAR CHECKS (STANDARD EXIT)
        prev_timestamp = float(prev.get("timestamp", prev.name))
        last_closed_bar = position.get("last_evaluated_closed_bar_id")
        
        if last_closed_bar is None or prev_timestamp > last_closed_bar:
            # A new bar just closed! Evaluate standard trailing lock exit on the CLOSED bar
            position["last_evaluated_closed_bar_id"] = prev_timestamp
            
            # Update trailing lock levels based on the closed bar's high/low
            # We use the previous bar because it just closed.
            closed_price = float(prev["close"])
            ladder_reason = check_atr_step_trailing_stop(position, frame, closed_price)
            if ladder_reason:
                logger.info(f"[STANDARD_EXIT] {position.get('symbol')} Trend exhausted at close. ({ladder_reason})")
                return ladder_reason
            else:
                logger.info(f"[HOLDING_WAIT_CLOSE] {position.get('symbol')} No close-based exit triggered. Holding for next candle.")

        return None

    def handle_post_exit_cleanup(self, position: Dict[str, Any], exit_reason: str):
        symbol = position.get("symbol", "UNKNOWN")
        logger.info(f"[Post-Exit Cleanup] 啟動平倉後狀態清理，原因: {exit_reason} (幣種: {symbol})")
        
        if "v10_phase_trailing" in position:
            position.pop("v10_phase_trailing")
        if "last_evaluated_closed_bar_id" in position:
            position.pop("last_evaluated_closed_bar_id")
            
        if exit_reason in ("EXIT_1.5_ATR_DEFENSE", "EXIT_MOMENTUM_REVERSAL_DEFENSE"):
            position["cooldown_mode"] = "WAIT_FOR_STABLE_KC"
            position["cooldown_kc_count"] = 2
        else:
            position["cooldown_mode"] = "NONE"
            
        position["force_space_reevaluation"] = True


def check_atr_step_trailing_stop(
    position: dict,
    frame: pd.DataFrame,
    price: float
) -> Optional[str]:
    try:
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
        
        if "defense_line" not in state:
            v8_reason = position.get("v8_reason", position.get("reason", ""))
            
            if side == "LONG":
                defense = entry_price - 1.5 * atr
                if "[STANDARD_ENTRY] Trend-Aligned MA Cross LONG" in v8_reason:
                    prev_low = float(frame.iloc[-2]['low'])
                    defense = min(defense, prev_low)
            else:
                defense = entry_price + 1.5 * atr
                if "[STANDARD_ENTRY] Trend-Aligned MA Cross SHORT" in v8_reason:
                    prev_high = float(frame.iloc[-2]['high'])
                    defense = max(defense, prev_high)
                
            state["defense_line"] = defense
            state["active_stop_price"] = defense
            state["last_locked_level"] = 0
            
            if v8_reason and ("[SPECIAL_ENTRY] Extreme Impulse" in v8_reason):
                state["last_locked_level"] = 1
                state["active_stop_price"] = entry_price

        last_locked_level = state.get("last_locked_level", 0)
        active_stop_price = state.get("active_stop_price", state["defense_line"])
        step_size_atr = 0.7
        
        # We update the lock level using the price (which is the close price passed from evaluate_bar_close)
        if side == "LONG":
            profit_distance = price - entry_price
            current_level = int(profit_distance // (step_size_atr * atr))
            if current_level > last_locked_level:
                state["last_locked_level"] = current_level
                new_stop = entry_price + ((current_level - 1) * step_size_atr * atr)
                state["active_stop_price"] = max(active_stop_price, new_stop)
                
        elif side == "SHORT":
            profit_distance = entry_price - price
            current_level = int(profit_distance // (step_size_atr * atr))
            if current_level > last_locked_level:
                state["last_locked_level"] = current_level
                new_stop = entry_price - ((current_level - 1) * step_size_atr * atr)
                state["active_stop_price"] = min(active_stop_price, new_stop)

        active_stop_price = state["active_stop_price"]
        
        # check trailing stop breach
        if side == "LONG" and price <= active_stop_price:
            return "EXIT_0.7_ATR_PROFIT_LOCK" if state["last_locked_level"] > 0 else "EXIT_1.5_ATR_DEFENSE"
        elif side == "SHORT" and price >= active_stop_price:
            return "EXIT_0.7_ATR_PROFIT_LOCK" if state["last_locked_level"] > 0 else "EXIT_1.5_ATR_DEFENSE"
                
    except Exception as e:
        logger.error(f"Error in check_atr_step_trailing_stop: {e}")
    return None
