"""Unified Entry Strategy evaluating streamlined entry methods.
Implements IEntryStrategy interface.
"""
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str]:
    """
    V7.0 動態自適應趨勢引擎 (雙軌進場邏輯)
    """
    if len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA"
        
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    live_price = float(live_price)
    
    # 軌道數據
    kc_upper = float(latest.get("kc_upper", live_price))
    kc_lower = float(latest.get("kc_lower", live_price))
    kc_middle = float(latest.get("kc_middle", live_price))
    
    # MA 數據
    ma3 = float(latest.get("ma3", live_price))
    ma15 = float(latest.get("ma15", live_price))
    ma15_slope = float(latest.get("ma15_slope", 0.0))
    
    # K 線數據 (取已收線的 prev 當作「反轉 K 線 / 扭頭 K 線」)
    prev_open = float(prev["open"])
    prev_close = float(prev["close"])
    prev_high = float(prev["high"])
    prev_low = float(prev["low"])
    
    prev_candle_height = prev_high - prev_low
    prev_body = abs(prev_close - prev_open)
    prev_body_ratio = prev_body / prev_candle_height if prev_candle_height > 0 else 0
    
    # 判斷 prev 顏色
    prev_is_bullish = prev_close > prev_open
    prev_is_bearish = prev_close < prev_open
    
    # 成交量數據 (判斷上一根已收線是否有放量)
    prev_vol = float(prev.get("volume", 0.0))
    prev_vol_ma_5 = float(prev.get("vol_ma_5", 0.0))
    is_volume_surge = prev_vol >= prev_vol_ma_5
    
    # ==========================================
    # 軌道 A：極值反轉 (大波段)
    # ==========================================
    track_a_ok = False
    track_a_reason = ""
    
    if side == "LONG":
        # 價格衝出 KC 下軌後，出現實體 >= 0.5 的反轉 K 線 (紅/陽線)，且突破 MA3
        if prev_low <= float(prev.get("kc_lower", live_price)) or live_price <= kc_lower:
            if prev_is_bullish and prev_body_ratio >= 0.5:
                if live_price > ma3:
                    if is_volume_surge:
                        track_a_ok = True
                        track_a_reason = "TRACK_A_EXTREME_REVERSAL_LONG"
    elif side == "SHORT":
        # 價格衝出 KC 上軌後，出現實體 >= 0.5 的反轉 K 線 (黑/陰線)，且跌破 MA3
        if prev_high >= float(prev.get("kc_upper", live_price)) or live_price >= kc_upper:
            if prev_is_bearish and prev_body_ratio >= 0.5:
                if live_price < ma3:
                    if is_volume_surge:
                        track_a_ok = True
                        track_a_reason = "TRACK_A_EXTREME_REVERSAL_SHORT"
                        

    # ==========================================
    # 軌道 B：中點回踩 (小波段)
    # ==========================================
    track_b_ok = False
    track_b_reason = ""
    
    # 檢查是否在 KC 內部
    is_inside_kc = (kc_lower < live_price < kc_upper)
    
    # MA15 攻擊角度 (斜率閾值設定)
    attack_slope_threshold = 1e-5
    
    if side == "LONG":
        if is_inside_kc:
            # 回測中軌或 MA15
            prev_kc_mid = float(prev.get("kc_middle", live_price))
            prev_ma15 = float(prev.get("ma15", live_price))
            touched_mid = (prev_low <= prev_kc_mid) or (prev_low <= prev_ma15)
            if touched_mid:
                # 實體 >= 0.3 扭頭
                if prev_is_bullish and prev_body_ratio >= 0.3:
                    # MA15 向上攻擊角度
                    if ma15_slope >= attack_slope_threshold:
                        if is_volume_surge:
                            track_b_ok = True
                            track_b_reason = "TRACK_B_MID_PULLBACK_LONG"
    elif side == "SHORT":
        if is_inside_kc:
            # 回測中軌或 MA15
            prev_kc_mid = float(prev.get("kc_middle", live_price))
            prev_ma15 = float(prev.get("ma15", live_price))
            touched_mid = (prev_high >= prev_kc_mid) or (prev_high >= prev_ma15)
            if touched_mid:
                # 實體 >= 0.3 扭頭
                if prev_is_bearish and prev_body_ratio >= 0.3:
                    # MA15 向下攻擊角度
                    if ma15_slope <= -attack_slope_threshold:
                        if is_volume_surge:
                            track_b_ok = True
                            track_b_reason = "TRACK_B_MID_PULLBACK_SHORT"
                            
    # ==========================================
    # 軌道 C：破軌突破 (強勢動能爆發)
    # ==========================================
    track_c_ok = False
    track_c_reason = ""
    
    if side == "LONG":
        # 價格衝破 KC 上軌
        if live_price > kc_upper:
            if prev_is_bullish and prev_body_ratio >= 0.3:
                if is_volume_surge:
                    track_c_ok = True
                    track_c_reason = "TRACK_C_BREAKOUT_LONG"
    elif side == "SHORT":
        # 價格衝破 KC 下軌
        if live_price < kc_lower:
            if prev_is_bearish and prev_body_ratio >= 0.3:
                if is_volume_surge:
                    track_c_ok = True
                    track_c_reason = "TRACK_C_BREAKOUT_SHORT"
                        
    track_reason = ""
    if track_a_ok:
        track_reason = track_a_reason
    elif track_b_ok:
        track_reason = track_b_reason
    elif track_c_ok:
        track_reason = track_c_reason
        
    if track_reason:
        return True, track_reason

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
