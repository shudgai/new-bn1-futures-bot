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
    
    velocity_drop_ratio = kwargs.get("velocity_drop_ratio", 0.0)

    # ==========================================
    # 動態趨勢適應 (Trend Strength Index - TSI)
    # ==========================================
    atr_expansion = False
    if len(df) >= 6:
        recent_atr = df['atr'].iloc[-3:].mean()
        prev_atr_val = df['atr'].iloc[-6:-3].mean()
        if prev_atr_val > 0 and (recent_atr / prev_atr_val - 1) >= 0.10:
            atr_expansion = True
            
    # 平滑處理：計算最近 3 根 K 線的 MA3 平均斜率
    ma3_slopes = []
    for i in range(-1, min(-5, -len(df)-1), -1):
        if len(df) >= abs(i) + 1:
            curr_ma3 = float(df.iloc[i].get("ma3", live_price))
            prior_ma3 = float(df.iloc[i-1].get("ma3", live_price))
            ma3_slopes.append(abs(curr_ma3 - prior_ma3))
        if len(ma3_slopes) >= 3:
            break
            
    avg_ma3_slope = sum(ma3_slopes) / len(ma3_slopes) if ma3_slopes else 0
    is_high_trend = (avg_ma3_slope >= 0.2 * atr) or atr_expansion
    trend_state = "High_Trend" if is_high_trend else "Low_Trend"

    if is_high_trend:
        # Aggressive Mode (High Trend)
        MAX_DIST_FROM_MID = 2.2
        VELOCITY_THRESHOLD = 0.40
        MIN_BANDWIDTH = 0.008
    else:
        # Conservative Mode (Low Trend)
        MAX_DIST_FROM_MID = 1.3
        VELOCITY_THRESHOLD = 0.20
        MIN_BANDWIDTH = 0.012

    # ==========================================
    # 第一道門檻：環境篩選 (The Environment Gate - A)
    # ==========================================
    kc_bandwidth = (kc_upper - kc_lower) / kc_middle if kc_middle > 0 else 0
    is_bandwidth_ok = (kc_bandwidth >= MIN_BANDWIDTH) or atr_expansion
    kc_slope_abs = abs(kc_middle - prev_kc_middle)
    is_slope_ok = kc_slope_abs >= 1e-5
    
    if not (is_bandwidth_ok and is_slope_ok):
        return False, f"WAIT_ENVIRONMENT_FLAT ({trend_state})"

    # ==========================================
    # 第二道門檻：空間與趨勢防禦 (The Structure Gate - B)
    # ==========================================
    ema_50_slope = ema_50 - prev_ema_50
    dist_from_mid = abs(live_price - kc_middle)
    
    if dist_from_mid > MAX_DIST_FROM_MID * atr:
        return False, f"REJECTED_OUTSIDE_ZONE ({trend_state})"
        
    is_velocity_slowdown = velocity_drop_ratio >= VELOCITY_THRESHOLD

    if side == "LONG":
        if ema_50_slope <= 0:
            return False, f"REJECTED_OUTSIDE_ZONE ({trend_state})"
            
        if live_price >= kc_upper:
            excess = live_price - kc_upper
            if not (excess <= 0.3 * atr and is_velocity_slowdown):
                return False, f"REJECTED_OUTSIDE_ZONE ({trend_state})"
        elif live_price <= kc_lower:
             return False, f"REJECTED_OUTSIDE_ZONE ({trend_state})"
             
    elif side == "SHORT":
        if ema_50_slope >= 0:
            return False, f"REJECTED_OUTSIDE_ZONE ({trend_state})"
            
        if live_price <= kc_lower:
            excess = kc_lower - live_price
            if not (excess <= 0.3 * atr and is_velocity_slowdown):
                return False, f"REJECTED_OUTSIDE_ZONE ({trend_state})"
        elif live_price >= kc_upper:
             return False, f"REJECTED_OUTSIDE_ZONE ({trend_state})"
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
        return False, f"WAIT_MOMENTUM_NOT_READY ({trend_state})"
        
    if not is_velocity_slowdown:
        return False, f"WAIT_MOMENTUM_NOT_READY ({trend_state})"
        
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
