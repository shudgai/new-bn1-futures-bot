import re

filepath = "core/services/exits/dual_track_exit_service.py"
with open(filepath, "r") as f:
    content = f.read()

# We will just write a whole new dual_track_exit_service.py since V9 is so much simpler.
new_content = """import pandas as pd
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
            
        # 1. 唯一平倉點 (峰谷反轉)
        exhaustion_reason = check_peak_exhaustion_exit(position, frame, price)
        if exhaustion_reason:
            return exhaustion_reason
            
        # 2. 保本機制 (1R Break-Even Only)
        be_reason = check_break_even_stop(position, frame, price)
        if be_reason:
            return be_reason
            
        # 3. 帳戶硬止損
        return check_hard_stop_exit(position, frame, price)


def check_emergency_exit(position: Dict[str, Any], frame: pd.DataFrame, price: float) -> Optional[str]:
    \"\"\"大瀑布與雙重異常 K 線 (斷路器)\"\"\"
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


def check_peak_exhaustion_exit(position: dict, frame: pd.DataFrame, price: float) -> Optional[str]:
    \"\"\"
    峰谷反轉：價格從軌道外收回軌道內 -> 產生大實體反轉 K 線(>= 0.5) -> 破壞 MA3
    完全使用最新一根「已收線 (Closed)」的 K 棒判斷。
    \"\"\"
    try:
        trade_phase = position.get("trade_phase", "TRENDING")
        if trade_phase != "EXHAUSTION_ZONE":
            return None
            
        side = position.get("side")
        last_closed = frame.iloc[-2] # 最新一根已經收線的 K 棒
        
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


def check_break_even_stop(position: dict, frame: pd.DataFrame, price: float) -> Optional[str]:
    \"\"\"
    保本機制：僅在獲利達到 1R 時，將止損點移動到「進場點」。一旦保本，止損點不再移動。
    \"\"\"
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
        
        # 狀態紀錄
        state = position.setdefault("v9_break_even_state", {})
        is_break_even_locked = state.get("is_break_even_locked", False)
        
        # 判斷是否鎖定保本
        if not is_break_even_locked and current_profit >= initial_risk:
            state["is_break_even_locked"] = True
            is_break_even_locked = True
            
        # 如果已經保本，檢查是否跌破進場點
        if is_break_even_locked:
            if side == "LONG" and price <= entry:
                return "BREAK_EVEN_STOP_LONG"
            elif side == "SHORT" and price >= entry:
                return "BREAK_EVEN_STOP_SHORT"
                
    except Exception:
        pass
    return None


def check_hard_stop_exit(position: dict, frame: pd.DataFrame, price: float) -> Optional[str]:
    \"\"\"硬止損\"\"\"
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
"""

with open(filepath, "w") as f:
    f.write(new_content)
