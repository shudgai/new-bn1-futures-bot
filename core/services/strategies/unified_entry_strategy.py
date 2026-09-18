"""Unified Entry Strategy evaluating streamlined entry methods.
Implements IEntryStrategy interface.
"""
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str]:
    """
    V9.0 動態自適應趨勢引擎 (多軌進場與空間過濾)
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
    is_volume_surge = prev_vol >= (1.2 * prev_vol_ma_5)  # 強化為 1.2 倍
    


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
    attack_slope_threshold = 1e-4  # 提高嚴格度
    
    if side == "LONG":
        if is_inside_kc:
            # 回測中軌或 MA15
            prev_kc_mid = float(prev.get("kc_middle", live_price))
            prev_ma15 = float(prev.get("ma15", live_price))
            touched_mid = (prev_low <= prev_kc_mid) or (prev_low <= prev_ma15)
            if touched_mid:
                # 實體 >= 0.4 扭頭 (強化確認)
                if prev_is_bullish and prev_body_ratio >= 0.4:
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
                # 實體 >= 0.4 扭頭 (強化確認)
                if prev_is_bearish and prev_body_ratio >= 0.4:
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
        # 1. 爆發點：上一根已收線 K 線必須實體破軌 (收盤在軌道外，實體比例 >= 0.3，放量)
        prev_kc_upper = float(prev.get("kc_upper", live_price))
        if prev_close > prev_kc_upper and prev_is_bullish and prev_body_ratio >= 0.3 and is_volume_surge:
            # 2. 站穩點：當前價格仍處於當前 KC 上軌外，且高於 MA3
            if live_price > kc_upper and live_price > ma3:
                track_c_ok = True
                track_c_reason = "TRACK_C_BREAKOUT_LONG"
    elif side == "SHORT":
        # 1. 爆發點：上一根已收線 K 線必須實體破軌 (收盤在軌道外，實體比例 >= 0.3，放量)
        prev_kc_lower = float(prev.get("kc_lower", live_price))
        if prev_close < prev_kc_lower and prev_is_bearish and prev_body_ratio >= 0.3 and is_volume_surge:
            # 2. 站穩點：當前價格仍處於當前 KC 下軌外，且低於 MA3
            if live_price < kc_lower and live_price < ma3:
                track_c_ok = True
                track_c_reason = "TRACK_C_BREAKOUT_SHORT"
                        
    # ==========================================
    # 軌道 D：趨勢延續 (Trend Continuation)
    # 適用場景：KC 通道方向已確立，價格在通道內緩慢順勢下滑/上升
    # 不要求放量（陰跌/陽漲往往無量），但要求「階梯式」實體方向
    # ==========================================
    track_d_ok = False
    track_d_reason = ""

    if len(df) >= 5:
        # L1：取最近 4 根已收線的 kc_middle，確認通道連續方向
        kc_mids = [float(df.iloc[i].get("kc_middle", 0) or df.iloc[i].get("ema_20", 0)) for i in range(-5, -1)]
        is_channel_down = all(kc_mids[i] > kc_mids[i + 1] for i in range(len(kc_mids) - 1))
        is_channel_up   = all(kc_mids[i] < kc_mids[i + 1] for i in range(len(kc_mids) - 1))

        # L2：最近 3 根已收線實體分析
        recent_bars = [df.iloc[i] for i in range(-4, -1)]  # 3 根已收線

        if side == "SHORT" and is_channel_down and ma3 < kc_middle:
            bearish_count = 0
            cascading = True
            prev_close_ref = None  # 只追蹤有效陰線的收盤位（過濾十字星干擾）
            for bar in recent_bars:
                b_open  = float(bar["open"]);  b_close = float(bar["close"])
                b_high  = float(bar["high"]);  b_low   = float(bar["low"])
                b_range = b_high - b_low
                b_body  = abs(b_close - b_open)
                b_ratio = b_body / b_range if b_range > 0 else 0
                if b_close < b_open and b_ratio >= 0.25:  # 有效陰線
                    bearish_count += 1
                    if prev_close_ref is not None and b_close >= prev_close_ref:
                        cascading = False  # 有效陰線中收盤未再創新低 → 不是階梯式
                    prev_close_ref = b_close  # 只用有效陰線更新參考點
            if bearish_count >= 2 and cascading:
                # L3：即時確認
                if live_price < kc_middle and live_price < ma3:
                    track_d_ok = True
                    track_d_reason = "TRACK_D_TREND_CONT_SHORT"

        elif side == "LONG" and is_channel_up and ma3 > kc_middle:
            bullish_count = 0
            cascading = True
            prev_close_ref = None  # 只追蹤有效陽線的收盤位
            for bar in recent_bars:
                b_open  = float(bar["open"]);  b_close = float(bar["close"])
                b_high  = float(bar["high"]);  b_low   = float(bar["low"])
                b_range = b_high - b_low
                b_body  = abs(b_close - b_open)
                b_ratio = b_body / b_range if b_range > 0 else 0
                if b_close > b_open and b_ratio >= 0.25:  # 有效陽線
                    bullish_count += 1
                    if prev_close_ref is not None and b_close <= prev_close_ref:
                        cascading = False  # 有效陽線中收盤未再創新高 → 不是階梯式
                    prev_close_ref = b_close  # 只用有效陽線更新參考點
            if bullish_count >= 2 and cascading:
                # L3：即時確認
                if live_price > kc_middle and live_price > ma3:
                    track_d_ok = True
                    track_d_reason = "TRACK_D_TREND_CONT_LONG"

    # 優先順序：A > C > B > D（D 只在前三者都無訊號時才觸發）
    track_reason = ""
    if track_a_ok:
        track_reason = track_a_reason
    elif track_c_ok:
        track_reason = track_c_reason
    elif track_b_ok:
        track_reason = track_b_reason
    elif track_d_ok:
        track_reason = track_d_reason
        
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
