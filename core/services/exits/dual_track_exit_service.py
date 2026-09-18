import pandas as pd
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# V10 狀態追蹤鍵列，持久化結構追蹤狀態
# v10_phase_trailing 取代 v10_ladder：改用 KC 三階段移動止損
DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close"]

class DualTrackExitStrategy:
    def __init__(self, account=None, fee: float = 0.0004, slippage: float = 0.0005):
        self.account = account
        self.fee = fee
        self.slippage = slippage

    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame, price: float, velocity_drop_ratio: float = 0.0, **kwargs) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        if not side:
            return None

        # 1. 極速動能反噬防禦 (最高優先級)
        reversal_defense_reason = check_momentum_reversal_defense(position, frame, price)
        if reversal_defense_reason:
            return reversal_defense_reason

        # 2. 帳戶硬止損 (極端防禦)
        hard_stop_reason = check_hard_stop_exit(position, frame, price)
        if hard_stop_reason:
            return hard_stop_reason

        # 3. 純機械式：動態防禦 (0.5 ATR) + 階梯限價鎖利 (1.0 ATR)
        ladder_reason = check_atr_step_trailing_stop(
            position, frame, price
        )
        if ladder_reason:
            return ladder_reason

        return None
            



def check_atr_step_trailing_stop(
    position: dict,
    frame: pd.DataFrame,
    price: float,
) -> Optional[str]:
    """
    動態防禦 (0.5 ATR) + 階梯限價鎖利 (1.0 ATR) 獨立掛單架構
    """
    try:
        import math
        side = position.get("side")
        entry_price = float(position.get("entry_price") or 0)
        
        if not side or entry_price <= 0 or frame is None or len(frame) == 0:
            return None
            
        last_row = frame.iloc[-1]
        atr = float(last_row.get("atr", 0))
        if atr <= 0:
            return None
            
        state = position.setdefault("v10_phase_trailing", {})
        
        # 1. 處理 0.5 ATR 初始防禦線 (永遠不變)
        if "defense_line" not in state:
            if side == "LONG":
                defense = entry_price - 0.5 * atr
            else:
                defense = entry_price + 0.5 * atr
            state["defense_line"] = defense
            symbol = position.get("symbol", "UNKNOWN")
            logger.info(f"[Protection] {symbol} 已於 {defense:.6g} 掛出 0.5 ATR 初始防禦線保護單")

        defense_line = state["defense_line"]
        
        # 2. 計算目前推移距離 (用來推進鎖利單)
        if side == "LONG":
            distance = price - entry_price
        else:
            distance = entry_price - price
            
        highest_dist = state.get("highest_distance", 0)
        if distance > highest_dist:
            highest_dist = distance
            state["highest_distance"] = highest_dist
            
        current_step = state.get("atr_step", 0)
        target_step = 0
        if highest_dist >= 1.0 * atr:
            target_step = math.floor(highest_dist / atr)
            
        # 3. 如果到達新階梯，撤銷舊單並掛出新單
        if target_step > current_step:
            state["atr_step"] = target_step
            if side == "LONG":
                new_lock = entry_price + (target_step - 1) * atr
            else:
                new_lock = entry_price - (target_step - 1) * atr
            
            # 若已有舊鎖利單，確保方向正確 (不會往下退)
            old_lock = state.get("profit_lock_line")
            if old_lock is not None:
                if side == "LONG":
                    new_lock = max(old_lock, new_lock)
                else:
                    new_lock = min(old_lock, new_lock)
                    
            state["profit_lock_line"] = new_lock
            symbol = position.get("symbol", "UNKNOWN")
            logger.info(f"[Take Profit Lock] {symbol} 撤銷上一張鎖利掛單，重新掛出新的鎖利單 (Limit Order) 於: {new_lock:.6g}")

        # 4. 判定是否觸發平倉 (先檢查鎖利單，再檢查防禦線)
        profit_lock = state.get("profit_lock_line")
        
        if side == "LONG":
            if profit_lock is not None and price <= profit_lock:
                return "EXIT_1.0_ATR_PROFIT_LOCK"
            if price <= defense_line:
                return "EXIT_0.5_ATR_DEFENSE"
        else:
            if profit_lock is not None and price >= profit_lock:
                return "EXIT_1.0_ATR_PROFIT_LOCK"
            if price >= defense_line:
                return "EXIT_0.5_ATR_DEFENSE"
                
    except Exception as e:
        logger.error(f"Error in check_atr_step_trailing_stop: {e}")
    return None




def check_hard_stop_exit(position: dict, frame: pd.DataFrame, price: float) -> Optional[str]:
    """硬止損"""
    try:
        side = position.get("side")
        initial_sl = float(position.get("initial_sl") or 0)
        if initial_sl > 0:
            if side == "LONG" and price <= initial_sl:
                return "EXIT_HARD_STOP_LONG"
            elif side == "SHORT" and price >= initial_sl:
                return "EXIT_HARD_STOP_SHORT"
    except Exception:
        pass
    return None


def check_momentum_reversal_defense(position: dict, frame: pd.DataFrame, price: float) -> Optional[str]:
    """極速動能反噬防禦 (Extreme Momentum Reversal Defense)"""
    try:
        side = position.get("side")
        if not side or frame is None or len(frame) < 3:
            return None
            
        latest = frame.iloc[-1]
        prev = frame.iloc[-2]
        
        atr = float(latest.get("atr", 0))
        if atr <= 0:
            return None
            
        # 取得最新一根K的屬性 (注意：這可能是未收線的即時報價，所以用 price 計算)
        latest_open = float(latest["open"])
        latest_high = max(float(latest["high"]), price)
        latest_low = min(float(latest["low"]), price)
        
        # 取得上一根K的屬性
        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        prev_body = abs(prev_close - prev_open)
        
        if side == "LONG":
            prev_is_bullish = prev_close > prev_open
            
            # 條件 A：實體吞沒 (Engulfing)
            latest_is_bearish = price < latest_open
            latest_body = latest_open - price
            if prev_is_bullish and latest_is_bearish:
                if latest_body >= prev_body:
                    prev_mid = (prev_close + prev_open) / 2
                    if price < prev_mid:
                        return "EXIT_MOMENTUM_REVERSAL_DEFENSE"
            
            # 條件 B：極端單棒暴跌 (Extreme Single Bar Crash)
            if (latest_high - price) > 1.5 * atr:
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE"
                
            # 條件 C：極速反轉 (Fast Reversal)
            two_bar_high = max(latest_high, float(prev["high"]))
            if (two_bar_high - price) > 1.0 * atr:
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE"
                
        elif side == "SHORT":
            prev_is_bearish = prev_close < prev_open
            
            # 條件 A：實體吞沒 (Engulfing)
            latest_is_bullish = price > latest_open
            latest_body = price - latest_open
            if prev_is_bearish and latest_is_bullish:
                if latest_body >= prev_body:
                    prev_mid = (prev_close + prev_open) / 2
                    if price > prev_mid:
                        return "EXIT_MOMENTUM_REVERSAL_DEFENSE"
            
            # 條件 B：極端單棒暴漲 (Extreme Single Bar Surge)
            if (price - latest_low) > 1.5 * atr:
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE"
                
            # 條件 C：極速反轉 (Fast Reversal)
            two_bar_low = min(latest_low, float(prev["low"]))
            if (price - two_bar_low) > 1.0 * atr:
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE"

    except Exception as e:
        logger.error(f"Error in check_momentum_reversal_defense: {e}")
        
    return None
