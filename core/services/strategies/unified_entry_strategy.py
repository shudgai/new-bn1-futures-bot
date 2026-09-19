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
            if dist_from_middle > 2.0 * current_atr:
                target_price = prev_close - (body_length * 0.5)
                return True, "[SPECIAL_ENTRY] Overextended Impulse LONG (Limit Order)", {"action": "ENTER_LIMIT", "target_price": target_price}
            return True, "[SPECIAL_ENTRY] Extreme Impulse LONG (>=2.0 ATR)", {"action": "ENTER"}
        elif side == "SHORT" and is_bearish:
            if is_strong_bull_trend:
                return False, "FILTERED_EXTREME_COUNTER_TREND: Fighting Strong Bullish Trend", {}
            if dist_from_middle > 2.0 * current_atr:
                target_price = prev_close + (body_length * 0.5)
                return True, "[SPECIAL_ENTRY] Overextended Impulse SHORT (Limit Order)", {"action": "ENTER_LIMIT", "target_price": target_price}
            return True, "[SPECIAL_ENTRY] Extreme Impulse SHORT (>=2.0 ATR)", {"action": "ENTER"}
        elif side == "LONG" and not is_bullish:
            pass # wrong side
        elif side == "SHORT" and not is_bearish:
            pass # wrong side
        else:
            return False, "FILTERED_EXTREME_DOJI", {}

    # =========================================================================
    # 軌道 B：標準精準進場路徑 (Standard Precision Path - 嚴格過濾)
    # =========================================================================
    # 1. 基礎動能過濾：實體比例必須 >= 60% (防長影線假突破)
    body_ratio = body_length / candle_range if candle_range > 0 else 0
    if body_ratio < 0.60:
        return False, "FILTERED_BODY_RATIO_LOW (<60%)", {}

    # 讀取 KC 數據與 MA 數據
    kc_upper_prev1 = float(prev_1.get("kc_upper", kc_mid_prev1))
    kc_lower_prev1 = float(prev_1.get("kc_lower", kc_mid_prev1))
    kc_upper_prev2 = float(prev_2.get("kc_upper", kc_mid_prev2))
    kc_lower_prev2 = float(prev_2.get("kc_lower", kc_mid_prev2))

    # 2. 通道動態指標
    current_width = kc_upper_prev1 - kc_lower_prev1
    prev_width = kc_upper_prev2 - kc_lower_prev2
    width_diff = current_width - prev_width
    is_expanding = width_diff > 0

    # -------------------------------------------------------------------------
    # 軌道 B-1：結構反轉進場 (修正版：MA5 + 大趨勢斜率對齊 + 空間緩衝)
    # -------------------------------------------------------------------------
    min_space_buffer_atr = 0.5
    buffer_threshold = min_space_buffer_atr * current_atr
    
    ma5_prev1 = float(prev_1.get('ma5', prev_1.get('ema_5', 0)))
    ma5_prev2 = float(prev_2.get('ma5', prev_2.get('ema_5', 0)))
    
    if ma5_prev1 == 0 and len(df) >= 5:
        # Fallback if ma5 is not pre-calculated
        ma5_series = df['close'].rolling(window=5).mean()
        ma5_prev1 = float(ma5_series.iloc[-2])
        ma5_prev2 = float(ma5_series.iloc[-3])

    has_ma = (ma5_prev1 > 0 and ma15_prev1 > 0 and ma5_prev2 > 0 and ma15_prev2 > 0)

    if has_ma:
        dist_from_middle_atr = abs(prev_close - kc_mid_prev1) / current_atr
        if dist_from_middle_atr >= 1.5:
            ma_cross_down = (ma5_prev2 >= ma15_prev2) and (ma5_prev1 < ma15_prev1)
            ma_cross_up   = (ma5_prev2 <= ma15_prev2) and (ma5_prev1 > ma15_prev1)

            if side == "SHORT" and ma_cross_down and is_bearish:
                if slope_ma15 <= 0 or slope_middle <= 0:
                    space_to_lower = prev_close - kc_lower_prev1
                    if space_to_lower < buffer_threshold:
                        return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT", {}
                    return True, "[STANDARD_ENTRY] Trend-Aligned MA Cross SHORT", {"action": "ENTER"}
                else:
                    return False, "FILTERED: Counter-trend MA cross (MA15/Middle rising)", {}
            if side == "LONG" and ma_cross_up and is_bullish:
                if slope_ma15 >= 0 or slope_middle >= 0:
                    space_to_upper = kc_upper_prev1 - prev_close
                    if space_to_upper < buffer_threshold:
                        return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT", {}
                    return True, "[STANDARD_ENTRY] Trend-Aligned MA Cross LONG", {"action": "ENTER"}
                else:
                    return False, "FILTERED: Counter-trend MA cross (MA15/Middle falling)", {}

    # -------------------------------------------------------------------------
    # 軌道 B-2：常規趨勢進場 (初始破軌 與 趨勢延續) 與 沿軌跟進 (Band Riding)
    # -------------------------------------------------------------------------
    prev2_close = float(prev_2['close'])
    cooldown_active = kwargs.get("cooldown_active", False)

    if dist_from_middle > 2.0 * current_atr:
        # ── 斜率特權豁免（Trend Privilege）────────────────────────────
        # 以 ATR 無量綱化斜率，門檻 0.5 代表「每根 K 棒中軌移動 0.5 倍 ATR」
        STRONG_SLOPE_THRESHOLD = 0.5
        norm_slope_ma15   = slope_ma15   / current_atr
        norm_slope_middle = slope_middle / current_atr

        privilege_long  = (norm_slope_ma15 > STRONG_SLOPE_THRESHOLD or norm_slope_middle > STRONG_SLOPE_THRESHOLD)
        privilege_short = (norm_slope_ma15 < -STRONG_SLOPE_THRESHOLD or norm_slope_middle < -STRONG_SLOPE_THRESHOLD)

        if side == "LONG" and is_bullish and privilege_long:
            return True, "[TREND_PRIVILEGE_ENTRY] Extreme Distance Waived (Strong Bull Slope) LONG", {"action": "ENTER", "is_privileged": True}

        if side == "SHORT" and is_bearish and privilege_short:
            return True, "[TREND_PRIVILEGE_ENTRY] Extreme Distance Waived (Strong Bear Slope) SHORT", {"action": "ENTER", "is_privileged": True}

        # 弱勢/震盪：依然攔截
        return False, "FILTERED_EXTREME_DISTANCE: Price too far from KC Middle (>2.0 ATR)", {}

    # --- 沿軌跟進 (Band Riding Re-entry) ---
    ma3_prev2 = float(prev_2.get('ma3', prev_2.get('ema_3', 0)))
    if is_strong_bear_trend and side == "SHORT" and is_bearish:
        pullback_touched_bear = (float(prev_2['high']) >= ma3_prev2) or (float(prev_2['high']) >= kc_lower_prev2)
        if pullback_touched_bear and (prev_close < kc_mid_prev1):
            return True, "[TREND_PRIVILEGE_ENTRY] Band Riding SHORT", {"action": "ENTER"}
            
    if is_strong_bull_trend and side == "LONG" and is_bullish:
        pullback_touched_bull = (float(prev_2['low']) <= ma3_prev2) or (float(prev_2['low']) <= kc_upper_prev2)
        if pullback_touched_bull and (prev_close > kc_mid_prev1):
            return True, "[TREND_PRIVILEGE_ENTRY] Band Riding LONG", {"action": "ENTER"}


    # -------------------------------------------------------------------------
    # 軌道 B-3：強趨勢回踩右側確認進場 (Pullback Re-entry)
    # 場景：強趨勢擴張中，出現一根回調棒（觸及上軌/MA3 支撐），
    #       下一根實體陽線突破前根高點 → 確認支撐有效，右側跟進。
    # -------------------------------------------------------------------------
    prev2_open  = float(prev_2["open"])
    prev2_close_val = float(prev_2["close"])
    prev2_high  = float(prev_2["high"])
    prev2_low   = float(prev_2["low"])

    if is_strong_bull_trend and side == "LONG" and is_bullish:
        # prev_2 是回調棒（陰線或縮量小陽），且低點觸及 KC 上軌支撐（1% 容差）或 MA3 支撐
        prev2_is_pullback = (prev2_close_val <= prev2_open) or (abs(prev2_close_val - prev2_open) < 0.3 * current_atr)
        pullback_touched_bull = (
            (prev2_low <= kc_upper_prev2 * 1.01) or   # 回調低點觸碰 KC 上軌（含 1% 緩衝）
            (ma3_prev2 > 0 and prev2_low <= ma3_prev2 * 1.01)  # 或觸碰 MA3 支撐
        )
        # prev_1 是實體陽線，且收盤突破 prev_2 的高點（右側確認）
        right_side_confirm = is_bullish and (body_ratio >= 0.4) and (prev_close > prev2_high)
        # 進場後仍有足夠空間
        space_to_upper = kc_upper_prev1 - prev_close
        has_space = space_to_upper >= buffer_threshold

        if prev2_is_pullback and pullback_touched_bull and right_side_confirm and has_space:
            return True, "[PULLBACK_RE_ENTRY] Strong Bull Trend Pullback Confirmed LONG", {"action": "ENTER"}

    if is_strong_bear_trend and side == "SHORT" and is_bearish:
        # prev_2 是回調棒（陽線或縮量小陰），且高點反彈觸及 KC 中軌阻力（1% 容差）或 MA3 阻力
        # ⚠️ 注意：空頭回調是「往上反彈」，阻力是 KC 中軌/MA3，而非 KC 下軌
        prev2_is_pullback = (prev2_close_val >= prev2_open) or (abs(prev2_close_val - prev2_open) < 0.3 * current_atr)
        kc_mid_prev2_val = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0)))
        # 回調反彈必須「測試阻力後失敗」：高點觸及 KC 中軌 or MA3，但收盤收在阻力之下
        pullback_tested_resistance = (
            (kc_mid_prev2_val > 0 and prev2_high >= kc_mid_prev2_val * 0.99) or   # 高點觸及 KC 中軌
            (ma3_prev2 > 0 and prev2_high >= ma3_prev2 * 0.99)                     # 或觸及 MA3 阻力
        )
        pullback_failed_resistance = (
            (kc_mid_prev2_val <= 0 or prev2_close_val < kc_mid_prev2_val) and  # 收盤未突破 KC 中軌
            (ma3_prev2 <= 0 or prev2_close_val < ma3_prev2)                     # 且收盤未突破 MA3
        )
        pullback_touched_bear = pullback_tested_resistance and pullback_failed_resistance
        # prev_1 是實體陰線，且收盤跌破 prev_2 的低點（右側確認）
        right_side_confirm = is_bearish and (body_ratio >= 0.4) and (prev_close < prev2_low)
        # 進場後仍有足夠空間
        space_to_lower = prev_close - kc_lower_prev1
        has_space = space_to_lower >= buffer_threshold

        if prev2_is_pullback and pullback_touched_bear and right_side_confirm and has_space:
            return True, "[PULLBACK_RE_ENTRY] Strong Bear Trend Pullback Confirmed SHORT", {"action": "ENTER"}

    if side == "LONG" and is_bullish:
        if slope_ma15 < 0 and slope_middle < 0:
            return False, "FILTERED_COUNTER_TREND_LONG (MA15 falling)", {}
            
        space_to_upper = kc_upper_prev1 - prev_close
        if space_to_upper < buffer_threshold and not is_strong_bull_trend:
            return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT", {}

        # 初始破軌：上穿中軌 + 通道擴張
        initial_break_long = (prev_close > kc_mid_prev1) and (prev2_close <= kc_mid_prev2)
        if initial_break_long and (is_expanding or slope_middle > 0) and not cooldown_active:
            return True, "[STANDARD_ENTRY] Initial Breakout LONG", {"action": "ENTER"}

        # 趨勢延續
        continuation_long = (prev_close > kc_mid_prev1) and (slope_middle > 0)
        
        is_strong_body = body_length >= (0.6 * current_atr if is_strong_bull_trend else 0.8 * current_atr)
        is_consecutive_above = (prev_close > kc_upper_prev1) and (prev2_close > kc_upper_prev2)
        
        has_momentum = is_strong_body or (is_strong_bull_trend and is_consecutive_above)
        
        if continuation_long and is_expanding and not cooldown_active and has_momentum:
            return True, "[STANDARD_ENTRY] Trend Continuation LONG", {"action": "ENTER"}

    elif side == "SHORT" and is_bearish:
        is_trend_aligned_short = (slope_ma15 <= 0) or (slope_middle <= 0)
        if not is_trend_aligned_short:
            return False, "FILTERED_COUNTER_TREND_SHORT: Fighting Strong Bullish Trend (MA15 rising)", {}
            
        space_to_lower = prev_close - kc_lower_prev1
        if space_to_lower < buffer_threshold and not is_strong_bear_trend:
            return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT", {}
            
        is_strong_body = body_length >= (0.6 * current_atr if is_strong_bear_trend else 0.8 * current_atr)
        is_consecutive_below = (prev_close < kc_lower_prev1) and (prev2_close < kc_lower_prev2)
        
        has_momentum = is_strong_body or (is_strong_bear_trend and is_consecutive_below)
        
        if not has_momentum:
            return False, "FILTERED_SHORT_MOMENTUM_WEAK", {}

        # 初始破軌
        initial_break_short = (prev_close < kc_mid_prev1) and (prev2_close >= kc_mid_prev2)
        if initial_break_short and (is_expanding or slope_middle < 0) and not cooldown_active:
            return True, "[STANDARD_ENTRY] Aligned Initial Breakout SHORT", {"action": "ENTER"}

        # 趨勢延續
        continuation_short = (prev_close < kc_mid_prev1) and (slope_middle < 0)
        if continuation_short and is_expanding and not cooldown_active:
            return True, "[STANDARD_ENTRY] Aligned Trend Continuation SHORT", {"action": "ENTER"}

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
