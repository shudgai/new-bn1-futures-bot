from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str]:
    """
    雙軌進場檢驗架構：
    - 軌道 A：特例快速進場路徑 (Extreme Volatility Path) -> 絕對優先、短路返回
    - 軌道 B：標準精準進場路徑 (Standard Precision Path) -> 多重確認、過濾雜訊
    """
    if df is None or len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA"

    latest = df.iloc[-1]       # 當前剛開盤或實時 K 棒
    prev_1 = df.iloc[-2]       # 剛收盤確認信號的 K 棒
    prev_2 = df.iloc[-3]       # 前一根對照 K 棒
    prev_3 = df.iloc[-4]

    current_atr = float(prev_1.get('atr', 0))
    if current_atr <= 0:
        return False, "INVALID_ATR"

    # K 棒幾何特徵計算
    prev_open = float(prev_1['open'])
    prev_close = float(prev_1['close'])
    prev_high = float(prev_1['high'])
    prev_low = float(prev_1['low'])

    body_length = abs(prev_close - prev_open)
    candle_range = prev_high - prev_low

    # 判斷多空方向 (收盤價 > 開盤價為陽線做多，反之為陰線做空)
    is_bullish = prev_close > prev_open
    is_bearish = prev_close < prev_open

    # =========================================================================
    # 軌道 A：特例快速進場路徑 (Extreme Volatility Path - 絕對優先)
    # =========================================================================
    if body_length >= 2.0 * current_atr:
        if side == "LONG" and is_bullish:
            return True, "[SPECIAL_ENTRY] Extreme Impulse LONG (>=2.0 ATR)"
        elif side == "SHORT" and is_bearish:
            return True, "[SPECIAL_ENTRY] Extreme Impulse SHORT (>=2.0 ATR)"
        elif side == "LONG" and not is_bullish:
            pass # wrong side
        elif side == "SHORT" and not is_bearish:
            pass # wrong side
        else:
            return False, "FILTERED_EXTREME_DOJI"

    # =========================================================================
    # 軌道 B：標準精準進場路徑 (Standard Precision Path - 嚴格過濾)
    # =========================================================================
    # 1. 基礎動能過濾：實體比例必須 >= 60% (防長影線假突破)
    body_ratio = body_length / candle_range if candle_range > 0 else 0
    if body_ratio < 0.60:
        return False, "FILTERED_BODY_RATIO_LOW (<60%)"

    # 讀取 KC 數據與 MA 數據
    kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0)))
    kc_mid_prev2 = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0)))
    
    kc_upper_prev1 = float(prev_1.get("kc_upper", kc_mid_prev1))
    kc_lower_prev1 = float(prev_1.get("kc_lower", kc_mid_prev1))
    kc_upper_prev2 = float(prev_2.get("kc_upper", kc_mid_prev2))
    kc_lower_prev2 = float(prev_2.get("kc_lower", kc_mid_prev2))

    # 2. 通道動態指標
    current_width = kc_upper_prev1 - kc_lower_prev1
    prev_width = kc_upper_prev2 - kc_lower_prev2
    width_diff = current_width - prev_width
    is_expanding = width_diff > 0

    slope_middle = kc_mid_prev1 - kc_mid_prev2

    # -------------------------------------------------------------------------
    # 軌道 B-1：結構反轉進場 (修正版：MA5 + 大趨勢斜率對齊 + 空間緩衝)
    # -------------------------------------------------------------------------
    min_space_buffer_atr = 0.5
    buffer_threshold = min_space_buffer_atr * current_atr
    
    ma5_prev1 = float(prev_1.get('ma5', prev_1.get('ema_5', 0)))
    ma5_prev2 = float(prev_2.get('ma5', prev_2.get('ema_5', 0)))
    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    
    if ma5_prev1 == 0 and len(df) >= 5:
        # Fallback if ma5 is not pre-calculated
        ma5_series = df['close'].rolling(window=5).mean()
        ma5_prev1 = float(ma5_series.iloc[-2])
        ma5_prev2 = float(ma5_series.iloc[-3])

    has_ma = (ma5_prev1 > 0 and ma15_prev1 > 0 and ma5_prev2 > 0 and ma15_prev2 > 0)
    slope_ma15 = ma15_prev1 - ma15_prev2

    if has_ma:
        dist_from_middle_atr = abs(prev_close - kc_mid_prev1) / current_atr
        if dist_from_middle_atr >= 1.5:
            ma_cross_down = (ma5_prev2 >= ma15_prev2) and (ma5_prev1 < ma15_prev1)
            ma_cross_up   = (ma5_prev2 <= ma15_prev2) and (ma5_prev1 > ma15_prev1)

            if side == "SHORT" and ma_cross_down and is_bearish:
                if slope_ma15 <= 0 or slope_middle <= 0:
                    space_to_lower = prev_close - kc_lower_prev1
                    if space_to_lower < buffer_threshold:
                        return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT"
                    return True, "[STANDARD_ENTRY] Trend-Aligned MA Cross SHORT"
                else:
                    return False, "FILTERED: Counter-trend MA cross (MA15/Middle rising)"
            if side == "LONG" and ma_cross_up and is_bullish:
                if slope_ma15 >= 0 or slope_middle >= 0:
                    space_to_upper = kc_upper_prev1 - prev_close
                    if space_to_upper < buffer_threshold:
                        return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT"
                    return True, "[STANDARD_ENTRY] Trend-Aligned MA Cross LONG"
                else:
                    return False, "FILTERED: Counter-trend MA cross (MA15/Middle falling)"

    # -------------------------------------------------------------------------
    # 軌道 B-2：常規趨勢進場 (初始破軌 與 趨勢延續)
    # -------------------------------------------------------------------------
    prev2_close = float(prev_2['close'])
    cooldown_active = kwargs.get("cooldown_active", False)

    dist_from_middle = abs(prev_close - kc_mid_prev1)
    if dist_from_middle > 2.0 * current_atr:
        return False, "FILTERED_EXTREME_DISTANCE: Price too far from KC Middle (>2.0 ATR)"

    if side == "LONG" and is_bullish:
        if slope_ma15 < 0 and slope_middle < 0:
            return False, "FILTERED_COUNTER_TREND_LONG (MA15 falling)"
            
        space_to_upper = kc_upper_prev1 - prev_close
        if space_to_upper < buffer_threshold:
            return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT"

        # 初始破軌：上穿中軌 + 通道擴張
        initial_break_long = (prev_close > kc_mid_prev1) and (prev2_close <= kc_mid_prev2)
        if initial_break_long and (is_expanding or slope_middle > 0) and not cooldown_active:
            return True, "[STANDARD_ENTRY] Initial Breakout LONG"

        # 趨勢延續
        continuation_long = (prev_close > kc_mid_prev1) and (slope_middle > 0)
        if continuation_long and is_expanding and not cooldown_active:
            return True, "[STANDARD_ENTRY] Trend Continuation LONG"

    elif side == "SHORT" and is_bearish:
        is_trend_aligned_short = (slope_ma15 <= 0) or (slope_middle <= 0)
        if not is_trend_aligned_short:
            return False, "FILTERED_COUNTER_TREND_SHORT: Fighting Strong Bullish Trend (MA15 rising)"
            
        space_to_lower = prev_close - kc_lower_prev1
        if space_to_lower < buffer_threshold:
            return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT"
            
        is_strong_body = body_length >= (0.8 * current_atr)
        is_consecutive_below = (prev_close < kc_mid_prev1) and (prev2_close < kc_mid_prev2)
        
        if not (is_strong_body or is_consecutive_below):
            return False, "FILTERED_SHORT_MOMENTUM_WEAK (Need >=0.8 ATR body or 2 bars below middle)"

        # 初始破軌
        initial_break_short = (prev_close < kc_mid_prev1) and (prev2_close >= kc_mid_prev2)
        if initial_break_short and (is_expanding or slope_middle < 0) and not cooldown_active:
            return True, "[STANDARD_ENTRY] Aligned Initial Breakout SHORT"

        # 趨勢延續
        continuation_short = (prev_close < kc_mid_prev1) and (slope_middle < 0)
        if continuation_short and is_expanding and not cooldown_active:
            return True, "[STANDARD_ENTRY] Aligned Trend Continuation SHORT"

    return False, "NO_VALID_ENTRY_SIGNAL"


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
