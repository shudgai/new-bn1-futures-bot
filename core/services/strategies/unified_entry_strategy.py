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
    
    prev_is_bullish = prev_close > prev_open
    prev_is_bearish = prev_close < prev_open
    
    # 取得中軌與 ATR
    kc_mid_prev = float(prev.get("kc_middle", prev.get("ema_20", 0)))
    kc_mid_prev_prev = float(prev_prev.get("kc_middle", prev_prev.get("ema_20", 0)))
    kc_mid_latest = float(latest.get("kc_middle", latest.get("ema_20", 0)))
    atr_prev = float(prev.get("atr", 0))

    if atr_prev <= 0 or kc_mid_prev == 0 or kc_mid_prev_prev == 0:
        return False, "WAIT_NO_DATA"
        
    # 計算通道寬度 (判斷擴張)
    kc_upper_prev = float(prev.get("kc_upper", kc_mid_prev))
    kc_lower_prev = float(prev.get("kc_lower", kc_mid_prev))
    kc_width_prev = kc_upper_prev - kc_lower_prev
    
    kc_upper_prev_prev = float(prev_prev.get("kc_upper", kc_mid_prev_prev))
    kc_lower_prev_prev = float(prev_prev.get("kc_lower", kc_mid_prev_prev))
    kc_width_prev_prev = kc_upper_prev_prev - kc_lower_prev_prev
    
    prev_body = abs(prev_close - prev_open)
    
    # 計算傾斜速度
    tilt_speed = abs(kc_mid_latest - kc_mid_prev)
    
    # 動態設定預期獲利空間門檻
    required_space_long = 1.5 * atr_prev
    if tilt_speed > 0.1 * atr_prev:
        required_space_long = 1.0 * atr_prev
        # 連續突破補償
        prev_prev_close = float(prev_prev["close"])
        if prev_close > kc_mid_prev and prev_prev_close > kc_mid_prev_prev:
            required_space_long = 0.8 * atr_prev

    required_space_short = 1.5 * atr_prev
    if tilt_speed > 0.1 * atr_prev:
        required_space_short = 1.0 * atr_prev
        # 連續突破補償
        prev_prev_close = float(prev_prev["close"])
        if prev_close < kc_mid_prev and prev_prev_close < kc_mid_prev_prev:
            required_space_short = 0.8 * atr_prev
    
    if side == "LONG":
        # === 0. 極端動能特權 (Extreme Momentum Privilege) ===
        if prev_body >= 2.0 * atr_prev and prev_is_bullish and prev_close > kc_mid_prev:
            return True, "SPECIAL_ENTRY_MOMENTUM_LONG"
            
        # 1. 基本趨勢判定 (前一根收盤價必須在中軌之上，且為陽線代表動能向上)
        if not (prev_is_bullish and prev_close > kc_mid_prev):
            return False, "WAIT_NOT_IN_BULL_TREND"
            
        # 2. 通道擴張與傾斜 (Expansion Check)
        is_tilting_up = kc_mid_latest > kc_mid_prev and kc_mid_prev > kc_mid_prev_prev
        is_expanding = kc_width_prev > kc_width_prev_prev
        if not (is_tilting_up and is_expanding):
            return False, "WAIT_NO_EXPANSION"
            
        # 3. 過濾假突破 (最新價絕對不能跌回中軌以內)
        if live_price <= kc_mid_latest:
            return False, "WAIT_PULLBACK_REJECTED"
            
        # 4. 預期獲利空間過濾 (Expected Profit Space, 動態門檻)
        kc_upper_latest = float(latest.get("kc_upper", live_price))
        expected_profit = kc_upper_latest - live_price
        if expected_profit < required_space_long:
            return False, "WAIT_PROFIT_SPACE_TOO_SMALL"
            
        return True, "DYNAMIC_TREND_LONG"
        
    elif side == "SHORT":
        # === 0. 極端動能特權 (Extreme Momentum Privilege) ===
        if prev_body >= 2.0 * atr_prev and prev_is_bearish and prev_close < kc_mid_prev:
            return True, "SPECIAL_ENTRY_MOMENTUM_SHORT"
            
        # 1. 基本趨勢判定 (前一根收盤價必須在中軌之下，且為陰線代表動能向下)
        if not (prev_is_bearish and prev_close < kc_mid_prev):
            return False, "WAIT_NOT_IN_BEAR_TREND"
            
        # 2. 通道擴張與傾斜 (Expansion Check)
        is_tilting_down = kc_mid_latest < kc_mid_prev and kc_mid_prev < kc_mid_prev_prev
        is_expanding = kc_width_prev > kc_width_prev_prev
        if not (is_tilting_down and is_expanding):
            return False, "WAIT_NO_EXPANSION"
            
        # 3. 過濾假突破 (最新價絕對不能漲回中軌以內)
        if live_price >= kc_mid_latest:
            return False, "WAIT_PULLBACK_REJECTED"
            
        # 4. 預期獲利空間過濾 (Expected Profit Space, 動態門檻)
        kc_lower_latest = float(latest.get("kc_lower", live_price))
        expected_profit = live_price - kc_lower_latest
        if expected_profit < required_space_short:
            return False, "WAIT_PROFIT_SPACE_TOO_SMALL"
            
        return True, "DYNAMIC_TREND_SHORT"
                
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
