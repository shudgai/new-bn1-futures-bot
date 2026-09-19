from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_structural_alignment(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
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

def check_extreme_pin_defense(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
    prev1_range = float(prev_1['high']) - float(prev_1['low'])
    prev1_body = abs(float(prev_1['close']) - float(prev_1['open']))
    
    prev2_range = float(prev_2['high']) - float(prev_2['low'])
    prev2_body = abs(float(prev_2['close']) - float(prev_2['open']))
    
    is_prev1_extreme = (prev1_range >= 1.5 * current_atr or prev1_body >= 1.5 * current_atr)
    is_prev2_extreme = (prev2_range >= 1.5 * current_atr or prev2_body >= 1.5 * current_atr)
    
    if is_prev2_extreme:
        if side == "LONG":
            if float(prev_1['close']) <= float(prev_2['close']):
                return False, "FILTERED_EXTREME_PIN: Next bar failed to close above extreme candle's close"
            else:
                return True, "OK"
        elif side == "SHORT":
            if float(prev_1['close']) >= float(prev_2['close']):
                return False, "FILTERED_EXTREME_PIN: Next bar failed to close below extreme candle's close"
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
    # 全局雙重共振守門員 (Global Dual Resonance Gatekeeper)
    # =========================================================================
    # 取消任何特例豁免，所有的開倉動作必須同時滿足 M3 與 MA15 同向
    is_aligned, reject_reason = check_structural_alignment(side, prev_1, prev_2, current_atr)
    if not is_aligned:
        return False, reject_reason, {}

    pin_passed, pin_reject_reason = check_extreme_pin_defense(side, prev_1, prev_2, current_atr)
    if not pin_passed:
        return False, pin_reject_reason, {}


    # =========================================================================
    # 軌道 V：V型轉折進場 (V-Shape Reversal Entry) - 最高優先級
    # =========================================================================
    if len(df) >= 25:
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
    
    if is_volume_burst:
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
    # 軌道 A：特例快速進場路徑 (Extreme Volatility Path - 絕對優先)
    # =========================================================================
    dist_from_middle = abs(prev_close - kc_mid_prev1)

    if body_length >= 1.3 * current_atr:
        if side == "LONG" and is_bullish:
            return True, "[SPECIAL_ENTRY] Extreme Impulse LONG (MARKET)", {"action": "ENTER"}
        elif side == "SHORT" and is_bearish:
            return True, "[SPECIAL_ENTRY] Extreme Impulse SHORT (MARKET)", {"action": "ENTER"}
        elif side == "LONG" and not is_bullish:
            pass # wrong side
        elif side == "SHORT" and not is_bearish:
            pass # wrong side
        else:
            return False, "FILTERED_EXTREME_DOJI", {}

    # =========================================================================
    # 軌道 P：平台破位優先特權 (Platform Breakdown Exemption)
    # =========================================================================
    if len(df) >= 6:
        # 取前 2~5 根 K 棒作為整理平台 (iloc[-6:-2])
        platform_bars = df.iloc[-6:-2]
        
        if side == "LONG":
            is_solid_breakout = is_bullish and (body_length >= 1.0 * current_atr)
            recent_max_high = float(platform_bars['high'].max())
            broke_platform = prev_close > recent_max_high
            
            # 平台破位判定 (已通過全局結構審查)
            if is_solid_breakout and broke_platform and prev_close > kc_mid_prev1:
                return True, "[PLATFORM_BREAKDOWN] Structural Bullish Breakout LONG", {"action": "ENTER"}
                
        elif side == "SHORT":
            is_solid_breakout = is_bearish and (body_length >= 1.0 * current_atr)
            recent_min_low = float(platform_bars['low'].min())
            broke_platform = prev_close < recent_min_low
            
            # 平台破位判定 (享有結構豁免權)
            if is_solid_breakout and broke_platform and prev_close < kc_mid_prev1:
                return True, "[PLATFORM_BREAKDOWN] Structural Bearish Breakout SHORT", {"action": "ENTER"}


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
