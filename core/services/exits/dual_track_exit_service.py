import pandas as pd
from typing import Dict, Any, Optional

class DualTrackExitStrategy:
    def __init__(self, account):
        self.account = account

    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame, price: float) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        if not side:
            return None

        # 追蹤 EXHAUSTION_ZONE
        trade_phase = position.get("trade_phase", "TRENDING")
        kc_upper = float(frame.iloc[-1].get("kc_upper", price))
        kc_lower = float(frame.iloc[-1].get("kc_lower", price))
        
        if trade_phase == "TRENDING":
            if side == "LONG" and price > kc_upper:
                position["trade_phase"] = "EXHAUSTION_ZONE"
            elif side == "SHORT" and price < kc_lower:
                position["trade_phase"] = "EXHAUSTION_ZONE"

        # 0. 災難斷路器 (最高優先)
        emergency_reason = check_emergency_exit(position, frame, price)
        if emergency_reason:
            return emergency_reason
            
        # 1. 獲利護衛型轉向 (Guarded Reversal - V10)
        reversal_reason = check_guarded_reversal_exit(position, frame, price)
        if reversal_reason:
            return reversal_reason
            
        # 2. 唯一平倉點 (峰谷反轉)
        exhaustion_reason = check_peak_exhaustion_exit(position, frame, price)
        if exhaustion_reason:
            return exhaustion_reason
            
        # 3. 結構式移動鎖利 (Swing Trailing - V10)
        trailing_reason = check_swing_trailing_exit(position, frame, price)
        if trailing_reason:
            return trailing_reason
            
        # 4. 帳戶硬止損
        return check_hard_stop_exit(position, frame, price)


def check_emergency_exit(position: Dict[str, Any], frame: pd.DataFrame, price: float) -> Optional[str]:
    """大瀑布與雙重異常 K 線 (斷路器)"""
    try:
        if len(frame) < 2:
            return None
        side = position.get("side")
        last = frame.iloc[-1]
        prev = frame.iloc[-2]
        
        atr_multiplier = 1.5
        waterfall_percent = 0.05
        
        last_close = float(last["close"])
        last_open = float(last["open"])
        last_atr = float(last.get("atr", price * 0.01))
        
        prev_close = float(prev["close"])
        prev_open = float(prev["open"])
        prev_atr = float(prev.get("atr", price * 0.01))
        
        v8_reason = position.get("v8_reason", "")
        is_breakout_entry = "TRACK_A" in v8_reason or "TRACK_C" in v8_reason
        
        if side == "LONG":
            def is_abnormal_bearish(o, c, atr):
                return (o - c) > (atr_multiplier * atr) and c < o
                
            is_double_crash = is_abnormal_bearish(prev_open, prev_close, prev_atr) and is_abnormal_bearish(last_open, last_close, last_atr)
            single_waterfall = (prev_close - last_close) > (prev_close * waterfall_percent)
            
            if single_waterfall:
                return "EXIT_EMERGENCY_WATERFALL_LONG"
            if is_double_crash and is_breakout_entry:
                return "EXIT_EMERGENCY_DOUBLE_ABNORMAL_LONG"
                
        elif side == "SHORT":
            def is_abnormal_bullish(o, c, atr):
                return (c - o) > (atr_multiplier * atr) and c > o
                
            is_double_crash = is_abnormal_bullish(prev_open, prev_close, prev_atr) and is_abnormal_bullish(last_open, last_close, last_atr)
            single_waterfall = (last_close - prev_close) > (prev_close * waterfall_percent)
            
            if single_waterfall:
                return "EXIT_EMERGENCY_WATERFALL_SHORT"
            if is_double_crash and is_breakout_entry:
                return "EXIT_EMERGENCY_DOUBLE_ABNORMAL_SHORT"
                
    except Exception:
        pass
    return None


def check_guarded_reversal_exit(position: dict, frame: pd.DataFrame, price: float) -> Optional[str]:
    """
    獲利護衛型轉向：
    1. 必須是獲利狀態。
    2. 價格放量衝破對側軌道。
    3. 返回 REVERSAL_EXIT 標籤讓引擎無縫接力。
    """
    try:
        side = position.get("side")
        entry_price = float(position.get("entry_price") or 0)
        
        # 1. 獲利護衛 (不獲利絕不凹單轉向)
        if side == "LONG" and price <= entry_price:
            return None
        if side == "SHORT" and price >= entry_price:
            return None
            
        last = frame.iloc[-1]
        kc_upper = float(last.get("kc_upper", price))
        kc_lower = float(last.get("kc_lower", price))
        current_vol = float(last.get("volume", 0.0))
        vol_ma_5 = float(last.get("vol_ma_5", 0.0))
        is_volume_surge = current_vol >= vol_ma_5
        
        # 2. 破軌判定
        if is_volume_surge:
            if side == "LONG" and price < kc_lower:
                return "REVERSAL_EXIT_OPPOSITE_RAIL_LONG"
            elif side == "SHORT" and price > kc_upper:
                return "REVERSAL_EXIT_OPPOSITE_RAIL_SHORT"
                
    except Exception:
        pass
    return None


def check_peak_exhaustion_exit(position: dict, frame: pd.DataFrame, price: float) -> Optional[str]:
    """
    峰谷反轉：價格從軌道外收回軌道內 -> 產生大實體反轉 K 線(>= 0.5) -> 破壞 MA3
    """
    try:
        trade_phase = position.get("trade_phase", "TRENDING")
        if trade_phase != "EXHAUSTION_ZONE":
            return None
            
        side = position.get("side")
        last_closed = frame.iloc[-2]
        
        c_open = float(last_closed["open"])
        c_close = float(last_closed["close"])
        c_high = float(last_closed["high"])
        c_low = float(last_closed["low"])
        
        c_height = c_high - c_low
        c_body = abs(c_close - c_open)
        body_ratio = c_body / c_height if c_height > 0 else 0
        
        kc_upper_closed = float(last_closed.get("kc_upper", price))
        kc_lower_closed = float(last_closed.get("kc_lower", price))
        ma3_closed = float(last_closed.get("ma3", price))
        
        if side == "LONG":
            back_inside = c_close < kc_upper_closed
            is_bearish = c_close < c_open and body_ratio >= 0.5
            break_ma3 = c_close < ma3_closed
            
            if back_inside and is_bearish and break_ma3:
                return "PEAK_EXHAUSTION_EXIT_LONG"
                
        elif side == "SHORT":
            back_inside = c_close > kc_lower_closed
            is_bullish = c_close > c_open and body_ratio >= 0.5
            break_ma3 = c_close > ma3_closed
            
            if back_inside and is_bullish and break_ma3:
                return "PEAK_EXHAUSTION_EXIT_SHORT"
                
    except Exception:
        pass
    return None


def check_swing_trailing_exit(position: dict, frame: pd.DataFrame, price: float) -> Optional[str]:
    """
    結構式移動鎖利 (Swing Trailing)：
    獲利 1R 後拉保本，接著追蹤波段極值(Swing High/Low)。
    """
    try:
        side = position.get("side")
        entry = float(position.get("entry_price") or 0)
        initial_sl = float(position.get("initial_sl") or 0)
        
        if entry <= 0 or initial_sl <= 0:
            return None
            
        initial_risk = abs(entry - initial_sl)
        if initial_risk <= 0:
            return None
            
        sign = 1 if side == "LONG" else -1
        current_profit = sign * (price - entry)
        
        state = position.setdefault("v10_swing_trailing", {})
        
        # 1. 1R 保本機制
        is_be_locked = state.get("is_break_even_locked", False)
        if not is_be_locked and current_profit >= initial_risk:
            state["is_break_even_locked"] = True
            state["trailing_sl"] = entry # 保本點
            is_be_locked = True
            
        # 2. 追蹤波段極值 (Swing High/Low)
        if is_be_locked:
            last = frame.iloc[-1]
            atr = float(last.get("atr", price * 0.01))
            
            # 以歷史最高/最低價作為 Swing Anchor
            highest_since = state.get("highest_price", entry)
            lowest_since = state.get("lowest_price", entry)
            
            if price > highest_since:
                state["highest_price"] = price
            if price < lowest_since:
                state["lowest_price"] = price
                
            current_trailing_sl = state.get("trailing_sl", entry)
            
            if side == "LONG":
                # 多單：隨著最高點上升，將止損點上移到「最高點 - 1.5 ATR」
                new_sl = state["highest_price"] - (1.5 * atr)
                if new_sl > current_trailing_sl:
                    state["trailing_sl"] = new_sl
                    
                if price <= state["trailing_sl"]:
                    return "EXIT_SWING_TRAILING_LONG"
                    
            elif side == "SHORT":
                # 空單：隨著最低點下降，將止損點下移到「最低點 + 1.5 ATR」
                new_sl = state["lowest_price"] + (1.5 * atr)
                if new_sl < current_trailing_sl or current_trailing_sl == entry:
                    state["trailing_sl"] = new_sl
                    
                if price >= state["trailing_sl"]:
                    return "EXIT_SWING_TRAILING_SHORT"
                
    except Exception:
        pass
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
