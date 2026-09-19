from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str, dict]:
    """
    雙軌進場檢驗架構：
    - 軌道 A：特例快速進場路徑 (Extreme Volatility Path) -> 絕對優先、短路返回
    - 軌道 B：標準精準進場路徑 (Standard Precision Path) -> 多重確認、過濾雜訊
    """
    if df is None or len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA", {}

    latest = df.iloc[-1]       # 當前剛開盤或實時 K 棒
    prev_1 = df.iloc[-2]       # 剛收盤確認信號的 K 棒
    prev_2 = df.iloc[-3]       # 前一根對照 K 棒
    prev_3 = df.iloc[-4]

    current_atr = float(prev_1.get('atr', 0))
    if current_atr <= 0:
        return False, "INVALID_ATR", {}

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

    kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0)))
    kc_mid_prev2 = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0)))
    slope_middle = kc_mid_prev1 - kc_mid_prev2

    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    slope_ma15 = ma15_prev1 - ma15_prev2

    # --- 強勢趨勢判定 (Strong Trend Detection) ---
    is_strong_bear_trend = (slope_ma15 < -0.05 * current_atr) and (slope_middle < -0.05 * current_atr)
    is_strong_bull_trend = (slope_ma15 > 0.05 * current_atr) and (slope_middle > 0.05 * current_atr)

    # =========================================================================
    # 軌道 A：特例快速進場路徑 (Extreme Volatility Path - 絕對優先)
    # =========================================================================
    dist_from_middle = abs(prev_close - kc_mid_prev1)

    if body_length >= 2.0 * current_atr:
        if side == "LONG" and is_bullish:
            if is_strong_bear_trend:
                return False, "FILTERED_EXTREME_COUNTER_TREND: Fighting Strong Bearish Trend", {}
            return True, "[SPECIAL_ENTRY] Extreme Impulse LONG (MARKET)", {"action": "ENTER"}
        elif side == "SHORT" and is_bearish:
            if is_strong_bull_trend:
                return False, "FILTERED_EXTREME_COUNTER_TREND: Fighting Strong Bullish Trend", {}
            return True, "[SPECIAL_ENTRY] Extreme Impulse SHORT (MARKET)", {"action": "ENTER"}
        elif side == "LONG" and not is_bullish:
            pass # wrong side
        elif side == "SHORT" and not is_bearish:
            pass # wrong side
        else:
            return False, "FILTERED_EXTREME_DOJI", {}

    # =========================================================================
    # 軌道 B-1：結構性爆發金叉 (Explosive MA Cross)
    # =========================================================================
    ma3_prev1 = float(prev_1.get('ma3', prev_1.get('ema_3', 0)))
    ma3_prev2 = float(prev_2.get('ma3', prev_2.get('ema_3', 0)))
    slope_ma3 = ma3_prev1 - ma3_prev2
    
    body_ratio = body_length / candle_range if candle_range > 0 else 0
    kc_upper_prev1 = float(prev_1.get("kc_upper", kc_mid_prev1))
    kc_lower_prev1 = float(prev_1.get("kc_lower", kc_mid_prev1))

    if ma3_prev1 > 0 and ma15_prev1 > 0 and ma3_prev2 > 0 and ma15_prev2 > 0:
        ma3_cross_up = (ma3_prev2 <= ma15_prev2) and (ma3_prev1 > ma15_prev1)
        ma3_cross_down = (ma3_prev2 >= ma15_prev2) and (ma3_prev1 < ma15_prev1)

        # 多頭金叉
        if side == "LONG" and is_bullish and ma3_cross_up:
            if (slope_ma3 / current_atr) >= 0.4 and body_ratio >= 0.60:
                if prev_close >= kc_mid_prev1:
                    is_v_shape_reversal = (body_length >= 2.0 * current_atr)
                    space_to_upper = kc_upper_prev1 - live_price
                    if not is_v_shape_reversal and space_to_upper < 1.0 * current_atr:
                        return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT: < 1.0 ATR", {}
                    
                    if is_v_shape_reversal:
                        return True, "[V_SHAPE_REVERSAL_ENTRY] Explosive V-Cross LONG", {"action": "ENTER"}
                    return True, "[STANDARD_ENTRY] Explosive MA Cross LONG", {"action": "ENTER"}

        # 空頭死叉
        if side == "SHORT" and is_bearish and ma3_cross_down:
            if (slope_ma3 / current_atr) <= -0.4 and body_ratio >= 0.60:
                if prev_close <= kc_mid_prev1:
                    is_v_shape_reversal = (body_length >= 2.0 * current_atr)
                    space_to_lower = live_price - kc_lower_prev1
                    if not is_v_shape_reversal and space_to_lower < 1.0 * current_atr:
                        return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT: < 1.0 ATR", {}
                        
                    if is_v_shape_reversal:
                        return True, "[V_SHAPE_REVERSAL_ENTRY] Explosive V-Cross SHORT", {"action": "ENTER"}
                    return True, "[STANDARD_ENTRY] Explosive Death Cross SHORT", {"action": "ENTER"}

    # =========================================================================
    # 軌道 B-2：強化結構破軌 (Structural Breakout)
    # =========================================================================
    # 1. 劇烈反噬冷卻檢測 (Post-Crash Cooldown)
    post_crash_cooldown_active = False
    for i in range(2, 5):
        if len(df) >= i + 1:
            h_bar = df.iloc[-i - 1]
            h_body = abs(float(h_bar['close']) - float(h_bar['open']))
            h_atr = float(h_bar.get('atr', current_atr))
            if h_body >= 2.0 * h_atr:
                post_crash_cooldown_active = True
                break
                
    if post_crash_cooldown_active:
        return False, "FILTERED_COOLDOWN: Post-Crash Cooldown Active", {}

    # 2. 結構轉折破軌進場
    kc_mid_prev2 = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0)))
    prev2_close = float(prev_2['close'])

    is_breakout_long = (prev_close > kc_upper_prev1) and is_bullish
    is_breakout_short = (prev_close < kc_lower_prev1) and is_bearish

    if side == "LONG" and is_breakout_long:
        if slope_ma15 >= 0:
            # 連續兩根收盤價在中軌上方
            if prev_close > kc_mid_prev1 and prev2_close > kc_mid_prev2:
                return True, "[STANDARD_ENTRY] Structural Breakout LONG", {"action": "ENTER"}
            else:
                return False, "FILTERED_BREAKOUT: Requires 2 consecutive closes above KC Middle", {}
        else:
            return False, "FILTERED_BREAKOUT: Counter-trend Breakout (MA15 slope falling)", {}

    if side == "SHORT" and is_breakout_short:
        if slope_ma15 <= 0:
            if prev_close < kc_mid_prev1 and prev2_close < kc_mid_prev2:
                return True, "[STANDARD_ENTRY] Structural Breakout SHORT", {"action": "ENTER"}
            else:
                return False, "FILTERED_BREAKOUT: Requires 2 consecutive closes below KC Middle", {}
        else:
            return False, "FILTERED_BREAKOUT: Counter-trend Breakout (MA15 slope rising)", {}

    return False, "NO_VALID_ENTRY_SIGNAL", {}


class UnifiedEntryStrategy(IEntryStrategy):
    def evaluate_entry(self, frame, price, side, **kwargs):
        if frame is None or len(frame) < 10 or ("kc_middle" not in frame.columns and "ema_20" not in frame.columns):
            return False, "WAIT_INSUFFICIENT_DATA_OR_INDICATORS", {"action": "WAIT"}

        try:
            ok, reason, action_dict = check_streamlined_entry_signal(frame, side, price, **kwargs)
        except Exception as e:
            return False, f"WAIT_ERROR_{e}", {"action": "WAIT"}

        if ok:
            base_dict = {"action": "ENTER", "side": side, "reason": reason}
            base_dict.update(action_dict)
            return True, reason, base_dict
        return False, reason, {"action": "WAIT"}
