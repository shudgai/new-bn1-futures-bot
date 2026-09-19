from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_structural_alignment(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
    """嚴格版（Track B/R/C 使用）：MA15 與 MA3 必須同向，無特例。"""
    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    ma3_prev1 = float(prev_1.get('ma3', prev_1.get('ema_3', 0)))
    ma3_prev2 = float(prev_2.get('ma3', prev_2.get('ema_3', 0)))
    
    slope_ma15 = ma15_prev1 - ma15_prev2
    slope_ma3 = ma3_prev1 - ma3_prev2
    
    if side == "LONG":
        ma15_ok = slope_ma15 >= 0
        ma3_ok = slope_ma3 > 1e-9
        if not ma15_ok: return False, "FILTERED_DUAL_RESONANCE: MA15 is falling"
        if not ma3_ok: return False, "FILTERED_DUAL_RESONANCE: MA3 not rising"
        return True, "OK"
        
    elif side == "SHORT":
        ma15_ok = slope_ma15 <= 0
        ma3_ok = slope_ma3 < -1e-9
        if not ma15_ok: return False, "FILTERED_DUAL_RESONANCE: MA15 is rising"
        if not ma3_ok: return False, "FILTERED_DUAL_RESONANCE: MA3 not falling"
        return True, "OK"
        
    return False, "INVALID_SIDE"


def check_structural_alignment_relaxed(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
    """寬鬆版（Track A / Track P 使用）：只要 MA15 沒有強烈反向即可通過。
    
    暴力破軌初期 MA15 因計算橫盤區間而滯後，此版本允許 MA15 持平或輕微逆向，
    只要 MA15 逆向斜率未超過 0.05 ATR 即視為「不強烈反向」，不攔截進場。
    MA3 仍須至少不強力反向（允許微弱逆向）。
    """
    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    ma3_prev1 = float(prev_1.get('ma3', prev_1.get('ema_3', 0)))
    ma3_prev2 = float(prev_2.get('ma3', prev_2.get('ema_3', 0)))

    slope_ma15 = ma15_prev1 - ma15_prev2
    slope_ma3 = ma3_prev1 - ma3_prev2
    # 強烈反向門檻：斜率超過 0.05 ATR 才視為崩盤式反向，否則放行
    strong_reversal_threshold = 0.05 * current_atr

    if side == "LONG":
        ma15_strongly_falling = slope_ma15 < -strong_reversal_threshold
        ma3_strongly_falling  = slope_ma3  < -strong_reversal_threshold
        if ma15_strongly_falling:
            return False, f"FILTERED_TRACK_AP: MA15 strongly falling ({slope_ma15:.6f} < -{strong_reversal_threshold:.6f})"
        if ma3_strongly_falling:
            return False, f"FILTERED_TRACK_AP: MA3 strongly falling ({slope_ma3:.6f} < -{strong_reversal_threshold:.6f})"
        return True, "OK"

    elif side == "SHORT":
        ma15_strongly_rising = slope_ma15 > strong_reversal_threshold
        ma3_strongly_rising  = slope_ma3  > strong_reversal_threshold
        if ma15_strongly_rising:
            return False, f"FILTERED_TRACK_AP: MA15 strongly rising ({slope_ma15:.6f} > {strong_reversal_threshold:.6f})"
        if ma3_strongly_rising:
            return False, f"FILTERED_TRACK_AP: MA3 strongly rising ({slope_ma3:.6f} > {strong_reversal_threshold:.6f})"
        return True, "OK"

    return False, "INVALID_SIDE"

def check_extreme_pin_defense(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
    prev1_range = float(prev_1['high']) - float(prev_1['low'])
    prev1_body = abs(float(prev_1['close']) - float(prev_1['open']))
    
    prev2_range = float(prev_2['high']) - float(prev_2['low'])
    prev2_body = abs(float(prev_2['close']) - float(prev_2['open']))
    
    is_prev1_extreme = (prev1_range >= 1.5 * current_atr or prev1_body >= 1.5 * current_atr)
    is_prev2_extreme = (prev2_range >= 1.5 * current_atr or prev2_body >= 1.5 * current_atr)
    
    if is_prev2_extreme:
        if side == "LONG":
            threshold = float(prev_2['open']) + 0.5 * prev2_body
            if float(prev_1['close']) <= threshold:
                return False, "FILTERED_EXTREME_PIN: Next bar failed to hold 50% of extreme candle's body"
            else:
                return True, "OK"
        elif side == "SHORT":
            threshold = float(prev_2['open']) - 0.5 * prev2_body
            if float(prev_1['close']) >= threshold:
                return False, "FILTERED_EXTREME_PIN: Next bar failed to hold 50% of extreme candle's body"
            else:
                return True, "OK"
                
    if is_prev1_extreme:
        return False, "FILTERED_EXTREME_PIN: Prev1 is extreme (>=1.5 ATR), waiting for next bar confirmation"
        
    return True, "OK"

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
    body_ratio = body_length / candle_range if candle_range > 0 else 0
    # 實體突破要求：實體長度必須佔整根 K 棒長度的 60% 以上，過濾長影線陷阱
    is_solid_body = (body_ratio >= 0.60)

    # 動能優先權 (Momentum Priority)：極端動能爆發 (實體波幅 >= 1.2 ATR 且實體比例 >= 0.7)
    is_extreme_momentum = (body_length >= 1.2 * current_atr) and (body_ratio >= 0.7)

    # 判斷多空方向 (收盤價 > 開盤價為陽線做多，反之為陰線做空)
    is_bullish = prev_close > prev_open
    is_bearish = prev_close < prev_open

    kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0)))
    kc_mid_prev2 = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0)))
    slope_middle = kc_mid_prev1 - kc_mid_prev2

    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    slope_ma15 = ma15_prev1 - ma15_prev2

    # =========================================================================
    # 區域進場封鎖 (Opposite Zone Entry Block)
    # 目的：避免在瀑布/噴發的極端行情中逆勢接刀。
    # =========================================================================
    kc_upper_live = float(latest.get("kc_upper", kc_mid_prev1))
    kc_lower_live = float(latest.get("kc_lower", kc_mid_prev1))
    
    if side == "LONG" and live_price < kc_lower_live:
        return False, "BLOCKED_OPPOSITE_ZONE_LONG: Price is below KC lower band", {}
    if side == "SHORT" and live_price > kc_upper_live:
        return False, "BLOCKED_OPPOSITE_ZONE_SHORT: Price is above KC upper band", {}

    # =========================================================================
    # 橫盤區間過濾 (Volatility Filter)
    # =========================================================================
    is_compression_zone = False
    if len(df) >= 7:
        past_5_bars = df.iloc[-6:-1]
        avg_atr_5 = float(past_5_bars['atr'].astype(float).mean())
        recent_5_high = float(past_5_bars['high'].astype(float).max())
        recent_5_low = float(past_5_bars['low'].astype(float).min())
        recent_5_range = recent_5_high - recent_5_low
        # 規則：5 根 K 棒的總振幅小於 1.5 倍平均 ATR，視為極度壓縮的橫盤雜訊區間
        if recent_5_range < (avg_atr_5 * 1.5):
            is_compression_zone = True

    # =========================================================================
    # 全局守門員：Track A/P 使用寬鬆版（MA15 不強烈反向即可）
    #             Track B/R/C/0/V 在各軌道前使用嚴格版
    # =========================================================================
    # 先用寬鬆版做初步篩查（攔截崩盤式反向）
    relaxed_aligned, relaxed_reject = check_structural_alignment_relaxed(side, prev_1, prev_2, current_atr)
    if not relaxed_aligned:
        return False, relaxed_reject, {}

    # 嚴格版結果暫存供 B/R/C 使用（不在此處直接攔截）
    strict_aligned, strict_reject = check_structural_alignment(side, prev_1, prev_2, current_atr)

    pin_passed, pin_reject_reason = check_extreme_pin_defense(side, prev_1, prev_2, current_atr)
    if not pin_passed:
        return False, pin_reject_reason, {}


    # =========================================================================
    # 軌道 V：V型轉折進場 (使用寬鬆守門員，已通過)
    # =========================================================================
    if len(df) >= 25 and not is_compression_zone:
        recent_20_bars = df.iloc[-21:-1]
        
        rsi_prev1 = float(prev_1.get('rsi', 50))
        rsi_prev2 = float(prev_2.get('rsi', 50))
        ma3_prev1 = float(prev_1.get('ma3', prev_1.get('ema_3', 0)))
        ma15_prev1 = float(prev_1.get('ma15', 0))
        ma3_prev2 = float(prev_2.get('ma3', prev_2.get('ema_3', 0)))
        ma15_prev2 = float(prev_2.get('ma15', 0))
        
        if side == "LONG":
            recent_low = float(recent_20_bars['low'].min())
            low_in_recent_3 = any(float(bar['low']) == recent_low for _, bar in df.iloc[-4:-1].iterrows())
            structure_break = recent_low < float(prev_1.get('kc_lower', kc_mid_prev1))
            ma_cross_up = (ma3_prev2 <= ma15_prev2) and (ma3_prev1 > ma15_prev1)
            rsi_rebound = (rsi_prev2 < 30) and (rsi_prev1 > rsi_prev2)
            
            if low_in_recent_3 and structure_break and ma_cross_up and rsi_rebound:
                return True, "[V_REVERSAL_ENTRY] Extreme Bottom V-Shape LONG", {"action": "ENTER"}
                
        elif side == "SHORT":
            recent_high = float(recent_20_bars['high'].max())
            high_in_recent_3 = any(float(bar['high']) == recent_high for _, bar in df.iloc[-4:-1].iterrows())
            structure_break = recent_high > float(prev_1.get('kc_upper', kc_mid_prev1))
            ma_cross_down = (ma3_prev2 >= ma15_prev2) and (ma3_prev1 < ma15_prev1)
            rsi_rebound = (rsi_prev2 > 70) and (rsi_prev1 < rsi_prev2)
            
            if high_in_recent_3 and structure_break and ma_cross_down and rsi_rebound:
                return True, "[V_REVERSAL_ENTRY] Extreme Top V-Shape SHORT", {"action": "ENTER"}

    # =========================================================================
    # 軌道 0：盤中動能預判 (Intra-bar Anticipation - 預防滑價與追高殺低)
    # =========================================================================
    latest_vol = float(latest.get("volume", 0))
    # 避免除以零，取過去兩根平均量
    avg_vol = (float(prev_1.get("volume", 1)) + float(prev_2.get("volume", 1))) / 2.0 + 1e-9
    is_volume_burst = latest_vol > avg_vol * 1.5
    
    if is_volume_burst and not is_compression_zone:
        kc_upper_live = float(latest.get("kc_upper", kc_mid_prev1))
        kc_lower_live = float(latest.get("kc_lower", kc_mid_prev1))
        
        # 預判條件：價格大幅度貫穿 (超越外軌 0.3 ATR) 且大趨勢強力支持
        if side == "LONG":
            if live_price > kc_upper_live + 0.3 * current_atr:
                return True, "[ANTICIPATED_ENTRY] Live Momentum Breakout LONG", {"action": "ENTER"}
        elif side == "SHORT":
            if live_price < kc_lower_live - 0.3 * current_atr:
                return True, "[ANTICIPATED_ENTRY] Live Momentum Breakout SHORT", {"action": "ENTER"}

    # =========================================================================
    # 軌道 A：特例快速進場（極端動能，使用寬鬆守門員，已通過）
    # 重構：突破根為 prev_2，確認根為 prev_1
    # 不受 is_compression_zone 限制，因為它是極端動能爆發
    # =========================================================================
    prev2_open = float(prev_2['open'])
    prev2_close = float(prev_2['close'])
    prev2_high = float(prev_2['high'])
    prev2_low = float(prev_2['low'])
    prev2_body = abs(prev2_close - prev2_open)
    prev2_range = prev2_high - prev2_low
    is_prev2_solid = (prev2_body / prev2_range >= 0.60) if prev2_range > 0 else False
    is_prev2_bullish = prev2_close > prev2_open
    is_prev2_bearish = prev2_close < prev2_open

    if prev2_body >= 1.2 * current_atr and is_prev2_solid:
        if side == "LONG" and is_prev2_bullish:
            # 放寬次根確認：prev_1 的收盤價只要高於 prev_2 實體的 50% 即可
            threshold = prev2_open + 0.5 * prev2_body
            if prev_close > threshold:
                return True, "[SPECIAL_ENTRY] Extreme Impulse LONG (relaxed next-bar)", {"action": "ENTER"}
        elif side == "SHORT" and is_prev2_bearish:
            threshold = prev2_open - 0.5 * prev2_body
            if prev_close < threshold:
                return True, "[SPECIAL_ENTRY] Extreme Impulse SHORT (relaxed next-bar)", {"action": "ENTER"}

    # =========================================================================
    # 軌道 P：平台破位（使用寬鬆守門員；次根確認放寬至 50% 緩衝）
    # 重構：突破根為 prev_2，確認根為 prev_1
    # 不受 is_compression_zone 限制
    # =========================================================================
    if len(df) >= 7:
        platform_bars = df.iloc[-7:-3] # prev_6 到 prev_3 為震盪平台

        if side == "LONG":
            is_solid_breakout = is_prev2_bullish and (prev2_body >= 1.0 * current_atr) and is_prev2_solid
            recent_max_high = float(platform_bars['high'].max())
            broke_platform = prev2_close > recent_max_high
            
            # ✅ 放寬：次根(prev_1)收盤只需超過平台最高點的 50% 緩衝位即可
            breakout_body = prev2_close - recent_max_high
            buffer_floor = recent_max_high - 0.5 * max(breakout_body, 0)
            
            if is_solid_breakout and broke_platform and prev_close > buffer_floor and prev_close > kc_mid_prev1:
                return True, "[PLATFORM_BREAKDOWN] Structural Bullish Breakout LONG (relaxed next-bar)", {"action": "ENTER"}

        elif side == "SHORT":
            is_solid_breakout = is_prev2_bearish and (prev2_body >= 1.0 * current_atr) and is_prev2_solid
            recent_min_low = float(platform_bars['low'].min())
            broke_platform = prev2_close < recent_min_low
            
            # ✅ 放寬：次根(prev_1)收盤只需低於平台最低點的 50% 緩衝位即可
            breakout_body = recent_min_low - prev2_close
            buffer_ceiling = recent_min_low + 0.5 * max(breakout_body, 0)
            
            if is_solid_breakout and broke_platform and prev_close < buffer_ceiling and prev_close < kc_mid_prev1:
                return True, "[PLATFORM_BREAKDOWN] Structural Bearish Breakout SHORT (relaxed next-bar)", {"action": "ENTER"}

    # =========================================================================
    # 若處於靜默模式（橫盤壓縮區），則在此處短路返回，屏蔽後續所有常規進場軌道
    # =========================================================================
    if is_compression_zone:
        return False, "FILTERED_COMPRESSION_ZONE: Market is in low volatility compression", {}

    # =========================================================================
    # 下方軌道 (B, R, C) 使用嚴格版守門員 (MA15 必須同向)
    # 動能優先權：若 K 棒具備極端動能 (is_extreme_momentum)，給予趨勢豁免權，無視嚴格對齊
    # =========================================================================
    if not strict_aligned and not is_extreme_momentum:
        return False, strict_reject, {}

    # =========================================================================
    # 軌道 B-1：結構性爆發金叉 (Explosive MA Cross)
    # =========================================================================
    ma3_prev1 = float(prev_1.get('ma3', prev_1.get('ema_3', 0)))
    ma3_prev2 = float(prev_2.get('ma3', prev_2.get('ema_3', 0)))
    slope_ma3 = ma3_prev1 - ma3_prev2
    
    kc_upper_prev1 = float(prev_1.get("kc_upper", kc_mid_prev1))
    kc_lower_prev1 = float(prev_1.get("kc_lower", kc_mid_prev1))

    if ma3_prev1 > 0 and ma15_prev1 > 0 and ma3_prev2 > 0 and ma15_prev2 > 0:
        ma3_cross_up = (ma3_prev2 <= ma15_prev2) and (ma3_prev1 > ma15_prev1)
        ma3_cross_down = (ma3_prev2 >= ma15_prev2) and (ma3_prev1 < ma15_prev1)

        # 多頭金叉
        if side == "LONG" and is_bullish and ma3_cross_up:
            if (slope_ma3 / current_atr) >= 0.4 and body_ratio >= 0.45:
                if prev_close >= kc_mid_prev1:
                    is_v_shape_reversal = (body_length >= 2.0 * current_atr)
                    space_to_upper = kc_upper_prev1 - live_price
                    if not is_v_shape_reversal and space_to_upper < 0.3 * current_atr:
                        return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT: < 0.3 ATR", {}
                    
                    if is_v_shape_reversal:
                        return True, "[V_SHAPE_REVERSAL_ENTRY] Explosive V-Cross LONG", {"action": "ENTER"}
                    return True, "[STANDARD_ENTRY] Explosive MA Cross LONG", {"action": "ENTER"}

        # 空頭死叉
        if side == "SHORT" and is_bearish and ma3_cross_down:
            if (slope_ma3 / current_atr) <= -0.4 and body_ratio >= 0.45:
                if prev_close <= kc_mid_prev1:
                    is_v_shape_reversal = (body_length >= 2.0 * current_atr)
                    space_to_lower = live_price - kc_lower_prev1
                    if not is_v_shape_reversal and space_to_lower < 0.3 * current_atr:
                        return False, "FILTERED_SPACE_BUFFER_TOO_TIGHT: < 0.3 ATR", {}
                        
                    if is_v_shape_reversal:
                        return True, "[V_SHAPE_REVERSAL_ENTRY] Explosive V-Cross SHORT", {"action": "ENTER"}
                    return True, "[STANDARD_ENTRY] Explosive Death Cross SHORT", {"action": "ENTER"}

    # =========================================================================
    # 軌道 B-2：強化結構破軌 (Structural Breakout)
    # =========================================================================
    prev1_range = prev_high - prev_low
    prev1_body = body_length
    is_prev1_extreme = (prev1_range >= 1.5 * current_atr or prev1_body >= 1.5 * current_atr)
    
    prev2_range = float(prev_2['high']) - float(prev_2['low'])
    prev2_body = abs(float(prev_2['close']) - float(prev_2['open']))
    is_prev2_extreme = (prev2_range >= 1.5 * current_atr or prev2_body >= 1.5 * current_atr)
    
    is_struct_long = (prev_close > kc_mid_prev1) and (prev_close > ma15_prev1)
    is_struct_short = (prev_close < kc_mid_prev1) and (prev_close < ma15_prev1)
    
    is_mom_long = is_bullish and (body_length >= 0.8 * current_atr) and (body_ratio >= 0.5)
    is_mom_short = is_bearish and (body_length >= 0.8 * current_atr) and (body_ratio >= 0.5)

    if side == "LONG":
        if is_struct_long and is_mom_long and not is_prev1_extreme:
            return True, "[STRUCTURAL_BREAKOUT] Momentum Breakout LONG", {"action": "ENTER"}
            
        is_struct_long2 = (float(prev_2['close']) > kc_mid_prev2) and (float(prev_2['close']) > ma15_prev2)
        is_mom_long2 = (float(prev_2['close']) > float(prev_2['open'])) and (prev2_body >= 0.8 * current_atr) and ((prev2_body / prev2_range) >= 0.5 if prev2_range > 0 else False)
        
        if is_struct_long2 and is_mom_long2 and is_prev2_extreme:
            if prev_close > float(prev_2['close']):
                return True, "[DELAYED_BREAKOUT] Momentum LONG", {"action": "ENTER"}
                
    elif side == "SHORT":
        if is_struct_short and is_mom_short and not is_prev1_extreme:
            return True, "[STRUCTURAL_BREAKOUT] Momentum Breakout SHORT", {"action": "ENTER"}
            
        is_struct_short2 = (float(prev_2['close']) < kc_mid_prev2) and (float(prev_2['close']) < ma15_prev2)
        is_mom_short2 = (float(prev_2['close']) < float(prev_2['open'])) and (prev2_body >= 0.8 * current_atr) and ((prev2_body / prev2_range) >= 0.5 if prev2_range > 0 else False)
        
        if is_struct_short2 and is_mom_short2 and is_prev2_extreme:
            if prev_close < float(prev_2['close']):
                return True, "[DELAYED_BREAKOUT] Momentum SHORT", {"action": "ENTER"}

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
    # =========================================================================
    # 軌道 R：中繼二次破位追單 (Trend Re-entry)
    # =========================================================================
    if len(df) >= 5:
        # 取得前 2~4 根 K 棒作為整理平台參考 (iloc[-5:-2])
        platform_bars = df.iloc[-5:-2] 
        curr_body_size = body_length
        
        if side == "LONG":
            # 趨勢鎖定 (MA15 強烈向上且價格在中軌上方)
            trend_up = (slope_ma15 > 0.05 * current_atr) and (prev_close > kc_mid_prev1)
            # 飽滿實體
            is_solid_green = is_bullish and (curr_body_size >= 0.8 * current_atr)
            # 突破整理平台
            recent_max_high = float(platform_bars['high'].max())
            broke_platform = prev_close > recent_max_high
            
            if trend_up and is_solid_green and broke_platform:
                return True, "[TREND_REENTRY] Bullish Continuation LONG", {"action": "ENTER"}
                
        elif side == "SHORT":
            # 趨勢鎖定 (MA15 強烈向下且價格在中軌下方)
            trend_down = (slope_ma15 < -0.05 * current_atr) and (prev_close < kc_mid_prev1)
            # 飽滿實體
            is_solid_red = is_bearish and (curr_body_size >= 0.8 * current_atr)
            # 跌破整理平台
            recent_min_low = float(platform_bars['low'].min())
            broke_platform = prev_close < recent_min_low
            
            if trend_down and is_solid_red and broke_platform:
                return True, "[TREND_REENTRY] Bearish Continuation SHORT", {"action": "ENTER"}
    # =========================================================================
    # 軌道 C：動能確認進場 (Momentum-Validated Entry)
    # =========================================================================
    # 時間窗口：回溯過去 3 根已收 K 棒 (prev_1, prev_2, prev_3)
    history_bars = [prev_1, prev_2, prev_3]
    
    # 尋找最近的「破軌基準點」
    breakout_anchor_close_long = None
    breakout_anchor_close_short = None
    
    # prev_1, prev_2, prev_3 依序檢查 (越近越好，找到即 break)
    for bar in history_bars:
        bar_close = float(bar['close'])
        bar_kc_upper = float(bar.get('kc_upper', kc_mid_prev1))
        bar_kc_lower = float(bar.get('kc_lower', kc_mid_prev1))
        
        if bar_close > bar_kc_upper and breakout_anchor_close_long is None:
            breakout_anchor_close_long = bar_close
            
        if bar_close < bar_kc_lower and breakout_anchor_close_short is None:
            breakout_anchor_close_short = bar_close

    # 動能驗證 (要求距離破位基準點至少 0.8 ATR 的位移)
    MOMENTUM_THRESHOLD = 0.8 * current_atr
    
    if side == "LONG" and breakout_anchor_close_long is not None:
        if (live_price - breakout_anchor_close_long) >= MOMENTUM_THRESHOLD:
            # 趨勢對齊確認
            if slope_ma15 >= 0: 
                return True, "[CONFIRMED_ENTRY] Momentum-Validated LONG", {"action": "ENTER"}
            else:
                return False, "FILTERED_CONFIRMATION: Counter-trend (MA15 falling)", {}
                
    if side == "SHORT" and breakout_anchor_close_short is not None:
        if (breakout_anchor_close_short - live_price) >= MOMENTUM_THRESHOLD:
            if slope_ma15 <= 0:
                return True, "[CONFIRMED_ENTRY] Momentum-Validated SHORT", {"action": "ENTER"}
            else:
                return False, "FILTERED_CONFIRMATION: Counter-trend (MA15 rising)", {}

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
