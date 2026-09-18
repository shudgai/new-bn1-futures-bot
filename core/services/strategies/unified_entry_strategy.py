"""Unified Entry Strategy evaluating streamlined entry methods.
Implements IEntryStrategy interface.
"""
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str]:
    if df is None or len(df) < 5:
        return False, ""
        
    prev = df.iloc[-2]
    prev_close = float(prev["close"])
    prev_open = float(prev["open"])
    prev_high = float(prev["high"])
    prev_low = float(prev["low"])
    
    prev_range = prev_high - prev_low
    prev_body = abs(prev_close - prev_open)
    prev_body_ratio = (prev_body / prev_range) if prev_range > 0 else 0
    
    prev_is_bullish = prev_close > prev_open
    prev_is_bearish = prev_close < prev_open
    
    latest = df.iloc[-1]
    kc_upper = float(latest.get("kc_upper", live_price))
    kc_lower = float(latest.get("kc_lower", live_price))
    ma3 = float(latest.get("ma3", live_price))
    
    # 破軌開倉唯一條件 (依據使用者最新指示)
    # 第一根實體破軌 (實體至少佔 20%)，第二根(最新價)站上ck外及MA3外才可以開倉
    
    if side == "LONG":
        prev_kc_upper = float(prev.get("kc_upper", live_price))
        # 1. 第一根(已收線)實體破軌，且實體佔比 >= 20%
        if prev_is_bullish and prev_body_ratio >= 0.2 and prev_close > prev_kc_upper and prev_open < prev_kc_upper:
            # 2. 第二根(最新價)站上 ck 外及 MA3 外
            if live_price > kc_upper and live_price > ma3:
                return True, "TRACK_C_BREAKOUT_LONG"
                
    elif side == "SHORT":
        prev_kc_lower = float(prev.get("kc_lower", live_price))
        # 1. 第一根(已收線)實體破軌，且實體佔比 >= 20%
        if prev_is_bearish and prev_body_ratio >= 0.2 and prev_close < prev_kc_lower and prev_open > prev_kc_lower:
            # 2. 第二根(最新價)站上 ck 外及 MA3 外
            if live_price < kc_lower and live_price < ma3:
                return True, "TRACK_C_BREAKOUT_SHORT"
                
    return False, "WAIT_NO_TRACK_SIGNAL"






class UnifiedEntryStrategy(IEntryStrategy):
    def evaluate_entry(self, frame, price, side, **kwargs):
        if frame is None or len(frame) < 10 or ("kc_middle" not in frame.columns and "ema_20" not in frame.columns):
            return False, "WAIT_INSUFFICIENT_DATA_OR_INDICATORS", {"action": "WAIT"}

        try:
            ok, reason = check_streamlined_entry_signal(frame, side, price, **kwargs)
        except Exception as e:
            return False, f"WAIT_ERROR_{e}", {"action": "WAIT"}

        if ok:
            return True, reason, {"action": "ENTER", "side": side, "reason": reason}
        return False, reason, {"action": "WAIT"}
