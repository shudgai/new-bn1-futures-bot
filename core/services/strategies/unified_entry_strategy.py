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
    prev_prev = df.iloc[-3]
    latest = df.iloc[-1]

    prev_close = float(prev["close"])
    prev_open = float(prev["open"])
    prev_high = float(prev["high"])
    prev_low = float(prev["low"])
    
    prev_range = prev_high - prev_low
    prev_body = abs(prev_close - prev_open)
    prev_body_ratio = (prev_body / prev_range) if prev_range > 0 else 0
    
    prev_is_bullish = prev_close > prev_open
    prev_is_bearish = prev_close < prev_open
    
    # 取得中軌與 ATR
    kc_mid_prev = float(prev.get("kc_middle", prev.get("ema_20", 0)))
    kc_mid_prev_prev = float(prev_prev.get("kc_middle", prev_prev.get("ema_20", 0)))
    kc_mid_latest = float(latest.get("kc_middle", latest.get("ema_20", 0)))
    atr_prev = float(prev.get("atr", 0))

    if atr_prev <= 0 or kc_mid_prev == 0 or kc_mid_prev_prev == 0:
        return False, "WAIT_NO_DATA"
    
    # 嚴格的狙擊手進場過濾 (使用者最新指示)
    if side == "LONG":
        # 1. 實體強勁突破 (實體佔比 >= 50%)
        if not (prev_is_bullish and prev_body_ratio >= 0.5):
            return False, "WAIT_NOT_STRONG_BODY"
            
        # 2. 收盤價突破中軌，且必須是「剛剛突破」 (開盤在中軌之下或附近)
        if prev_close <= kc_mid_prev or prev_open > (kc_mid_prev + 0.2 * atr_prev):
            return False, "WAIT_NO_BREAKOUT_OR_TOO_LATE"
            
        # 3. 通道傾斜 (Expansion) - 必須向上
        if kc_mid_prev <= kc_mid_prev_prev:
            return False, "WAIT_NO_EXPANSION"
            
        # 4. 突破距離確認 (距離中軌 >= 0.5 ATR)，但不能偏離過遠 (<= 1.5 ATR) 避免追高
        dist = prev_close - kc_mid_prev
        if dist < (0.5 * atr_prev) or dist > (1.5 * atr_prev):
            return False, "WAIT_DISTANCE_INVALID"
            
        # 5. 過濾假突破 (最新價絕對不能回補中軌以內)
        if live_price <= kc_mid_latest:
            return False, "WAIT_PULLBACK_REJECTED"
            
        # 6. 預期獲利空間過濾 (Expected Profit Space)
        kc_upper_latest = float(latest.get("kc_upper", live_price))
        expected_profit = kc_upper_latest - live_price
        if expected_profit < (1.5 * atr_prev):
            return False, "WAIT_PROFIT_SPACE_TOO_SMALL"
            
        return True, "SNIPER_BREAKOUT_LONG"
        
    elif side == "SHORT":
        # 1. 實體強勁突破 (實體佔比 >= 50%)
        if not (prev_is_bearish and prev_body_ratio >= 0.5):
            return False, "WAIT_NOT_STRONG_BODY"
            
        # 2. 收盤價突破中軌，且必須是「剛剛突破」 (開盤在中軌之上或附近)
        if prev_close >= kc_mid_prev or prev_open < (kc_mid_prev - 0.2 * atr_prev):
            return False, "WAIT_NO_BREAKOUT_OR_TOO_LATE"
            
        # 3. 通道傾斜 (Expansion) - 必須向下
        if kc_mid_prev >= kc_mid_prev_prev:
            return False, "WAIT_NO_EXPANSION"
            
        # 4. 突破距離確認 (距離中軌 >= 0.5 ATR)，但不能偏離過遠 (<= 1.5 ATR) 避免追低
        dist = kc_mid_prev - prev_close
        if dist < (0.5 * atr_prev) or dist > (1.5 * atr_prev):
            return False, "WAIT_DISTANCE_INVALID"
            
        # 5. 過濾假突破 (最新價絕對不能回補中軌以內)
        if live_price >= kc_mid_latest:
            return False, "WAIT_PULLBACK_REJECTED"
            
        # 6. 預期獲利空間過濾 (Expected Profit Space)
        kc_lower_latest = float(latest.get("kc_lower", live_price))
        expected_profit = live_price - kc_lower_latest
        if expected_profit < (1.5 * atr_prev):
            return False, "WAIT_PROFIT_SPACE_TOO_SMALL"
            
        return True, "SNIPER_BREAKOUT_SHORT"
                
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
