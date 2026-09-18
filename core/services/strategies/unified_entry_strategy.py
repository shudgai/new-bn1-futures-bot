"""Unified Entry Strategy evaluating streamlined entry methods.
Implements IEntryStrategy interface.
"""
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str]:
    """
    V5.2 ABC Dynamic Defense and Profit Engine (Three-Gate Gatekeeper)
    """
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else latest
    
    live_price = float(live_price)
    atr = float(latest.get("atr", live_price * 0.01))
    kc_upper = float(latest.get("kc_upper", live_price))
    kc_lower = float(latest.get("kc_lower", live_price))
    kc_middle = float(latest.get("kc_middle", latest.get("ema_20", live_price)))
    prev_kc_middle = float(prev.get("kc_middle", prev.get("ema_20", live_price)))
    ema_50 = float(latest.get("ema_50", live_price))
    prev_ema_50 = float(prev.get("ema_50", live_price))
    
    velocity_slowdown = kwargs.get("velocity_slowdown", False)

    # ==========================================
    # 第一道門檻：環境篩選 (The Environment Gate - A)
    # ==========================================
    kc_bandwidth = (kc_upper - kc_lower) / kc_middle if kc_middle > 0 else 0
    atr_expansion = False
    if len(df) >= 6:
        recent_atr = df['atr'].iloc[-3:].mean()
        prev_atr = df['atr'].iloc[-6:-3].mean()
        if prev_atr > 0 and (recent_atr / prev_atr - 1) >= 0.20:
            atr_expansion = True
            
    is_bandwidth_ok = (kc_bandwidth >= 0.012) or atr_expansion
    kc_slope_abs = abs(kc_middle - prev_kc_middle)
    is_slope_ok = kc_slope_abs >= 1e-5
    
    if not (is_bandwidth_ok and is_slope_ok):
        return False, "WAIT_ENVIRONMENT_FLAT"

    # ==========================================
    # 第二道門檻：空間與趨勢防禦 (The Structure Gate - B)
    # ==========================================
    ema_50_slope = ema_50 - prev_ema_50
    dist_from_mid = abs(live_price - kc_middle)
    
    if dist_from_mid > 1.5 * atr:
        return False, "REJECTED_OUTSIDE_ZONE"
        
    if side == "LONG":
        if ema_50_slope <= 0:
            return False, "REJECTED_OUTSIDE_ZONE"
            
        if live_price >= kc_upper:
            excess = live_price - kc_upper
            if not (excess <= 0.3 * atr and velocity_slowdown):
                return False, "REJECTED_OUTSIDE_ZONE"
        elif live_price <= kc_lower:
             return False, "REJECTED_OUTSIDE_ZONE"
             
    elif side == "SHORT":
        if ema_50_slope >= 0:
            return False, "REJECTED_OUTSIDE_ZONE"
            
        if live_price <= kc_lower:
            excess = kc_lower - live_price
            if not (excess <= 0.3 * atr and velocity_slowdown):
                return False, "REJECTED_OUTSIDE_ZONE"
        elif live_price >= kc_upper:
             return False, "REJECTED_OUTSIDE_ZONE"
    else:
        return False, "INVALID_SIDE"

    # ==========================================
    # 第三道門檻：動能與實體驗證 (The Momentum Gate - C)
    # ==========================================
    latest_open = float(latest["open"])
    latest_close = float(latest["close"])
    latest_high = float(latest["high"])
    latest_low = float(latest["low"])
    
    candle_height = latest_high - latest_low
    solid_body_ratio = abs(latest_close - latest_open) / candle_height if candle_height > 0 else 0
    
    if solid_body_ratio < 0.50:
        return False, "WAIT_MOMENTUM_NOT_READY"
        
    if not velocity_slowdown:
        return False, "WAIT_MOMENTUM_NOT_READY"
        
    return True, "ALLOW_ENTRY_LIMIT"


class UnifiedEntryStrategy(IEntryStrategy):
    def evaluate_entry(self, frame, price, side, **kwargs):
        if frame is None or len(frame) < 10 or ("kc_middle" not in frame.columns and "ema_20" not in frame.columns):
            return False, "WAIT_INSUFFICIENT_DATA_OR_INDICATORS", {"action": "WAIT"}

        try:
            ok, reason = check_streamlined_entry_signal(frame, side, price, **kwargs)
        except Exception:
            return False, "WAIT_INSUFFICIENT_INDICATORS", {"action": "WAIT"}

        if ok:
            return True, reason, {"action": "ENTER", "side": side, "reason": reason}
        return False, reason, {"action": "WAIT"}
