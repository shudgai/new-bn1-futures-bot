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
    雙軌進場檢驗架構 (嚴格版)：
    進場必須滿足以下兩個條件之一，才能觸發開倉：
    - 門檻 A：結構破位 (Structure Break)
    - 門檻 B：動能轉向 (Momentum Cross)
    沒有這兩個門檻，絕不開倉。
    """
    if df is None or len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA", {}

    latest = df.iloc[-1]       # 當前剛開盤或實時 K 棒
    prev_1 = df.iloc[-2]       # 剛收盤確認信號的 K 棒
    prev_2 = df.iloc[-3]       # 前一根對照 K 棒

    current_atr = float(prev_1.get('atr', 0))
    if current_atr <= 0:
        return False, "INVALID_ATR", {}

    kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0)))
    kc_upper_live = float(latest.get("kc_upper", kc_mid_prev1))
    kc_lower_live = float(latest.get("kc_lower", kc_mid_prev1))
    
    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    ma3_prev1 = float(prev_1.get('ma3', prev_1.get('ema_3', 0)))
    ma3_prev2 = float(prev_2.get('ma3', prev_2.get('ema_3', 0)))

    # =========================================================================
    # 盤整過濾器 (Consolidation Filter)
    # 確保只在有動能的狀態下開倉，避免橫盤區間的虛假破位
    # =========================================================================
    lookback = min(len(df) - 1, 6)  # 取最近 6 根已收線 K 棒 (約 30~90 分鐘)
    if lookback > 0:
        recent_bars_for_consolidation = df.iloc[-lookback-1:-1]
        consolidation_high = float(recent_bars_for_consolidation['high'].max())
        consolidation_low = float(recent_bars_for_consolidation['low'].min())
        consolidation_range = consolidation_high - consolidation_low
        
        # 門檻：波動範圍小於 0.5 * ATR 視為盤整區
        consolidation_threshold = 0.5 * current_atr
        
        if consolidation_range < consolidation_threshold:
            return False, f"FILTERED_CONSOLIDATION: Price range ({consolidation_range:.6f}) < threshold ({consolidation_threshold:.6f}), blocked fake breakout", {}

    # =========================================================================
    # 門檻 A：結構破位 (Structure Breakout)
    # 條件：價格實質性地破位，且為了過濾假突破，必須伴隨「連續兩根實體 K 線」站穩在軌道外。
    # =========================================================================
    passed_structure_break = False
    structure_reason = ""
    
    # 判斷 K 線是否為實體 (排除十字星)
    prev1_body = abs(float(prev_1['close']) - float(prev_1['open']))
    prev2_body = abs(float(prev_2['close']) - float(prev_2['open']))
    prev1_is_solid = prev1_body >= 0.15 * current_atr
    prev2_is_solid = prev2_body >= 0.15 * current_atr
    
    kc_lower_prev1 = float(prev_1.get("kc_lower", kc_mid_prev1))
    kc_upper_prev1 = float(prev_1.get("kc_upper", kc_mid_prev1))
    kc_lower_prev2 = float(prev_2.get("kc_lower", kc_mid_prev1))
    kc_upper_prev2 = float(prev_2.get("kc_upper", kc_mid_prev1))

    if side == "LONG":
        prev1_outside = float(prev_1['close']) < kc_lower_prev1
        prev2_outside = float(prev_2['close']) < kc_lower_prev2
        
        if live_price < kc_lower_live and prev1_outside and prev2_outside and prev1_is_solid and prev2_is_solid:
            passed_structure_break = True
            structure_reason = "[STRUCTURAL_BREAK] Price strictly broke lower KC band with 2 consecutive solid candles (LONG)"
            
    elif side == "SHORT":
        prev1_outside = float(prev_1['close']) > kc_upper_prev1
        prev2_outside = float(prev_2['close']) > kc_upper_prev2
        
        if live_price > kc_upper_live and prev1_outside and prev2_outside and prev1_is_solid and prev2_is_solid:
            passed_structure_break = True
            structure_reason = "[STRUCTURAL_BREAK] Price strictly broke upper KC band with 2 consecutive solid candles (SHORT)"

    # =========================================================================
    # 門檻 B：動能轉向 (Momentum Cross)
    # 必須滿足「峰谷轉向（先升後跌）」，且同時伴隨「MA3 與 MA15 發生明顯交叉（或同向轉向）」。
    # =========================================================================
    passed_momentum_cross = False
    momentum_reason = ""
    
    # 計算峰谷轉向
    recent_bars = df.iloc[-5:-1]
    recent_high = float(recent_bars['high'].max())
    recent_low = float(recent_bars['low'].min())
    
    # 先跌後升 (LONG)：價格比近期低點明顯回升
    pivot_turn_long = live_price > recent_low + (0.1 * current_atr) 
    # 先升後跌 (SHORT)：價格比近期高點明顯回落
    pivot_turn_short = live_price < recent_high - (0.1 * current_atr) 
    
    # 均線交叉或同向確認
    ma3_cross_up = (ma3_prev2 <= ma15_prev2) and (ma3_prev1 > ma15_prev1)
    ma3_cross_down = (ma3_prev2 >= ma15_prev2) and (ma3_prev1 < ma15_prev1)
    ma3_trend_up = (ma3_prev1 > ma15_prev1) and (ma3_prev1 - ma3_prev2 > 0.02 * current_atr)
    ma3_trend_down = (ma3_prev1 < ma15_prev1) and (ma3_prev2 - ma3_prev1 > 0.02 * current_atr)
    
    if side == "LONG":
        if pivot_turn_long and (ma3_cross_up or ma3_trend_up):
            passed_momentum_cross = True
            momentum_reason = "[MOMENTUM_CROSS] MA3 crossed/trended UP with Pivot Turn (LONG)"
    elif side == "SHORT":
        if pivot_turn_short and (ma3_cross_down or ma3_trend_down):
            passed_momentum_cross = True
            momentum_reason = "[MOMENTUM_CROSS] MA3 crossed/trended DOWN with Pivot Turn (SHORT)"

    # =========================================================================
    # 最終進場許可閘門
    # =========================================================================
    if passed_structure_break:
        return True, structure_reason, {"action": "ENTER"}
    if passed_momentum_cross:
        return True, momentum_reason, {"action": "ENTER"}

    return False, "FILTERED_NO_STRUCTURAL_OR_MOMENTUM_CONFIRMATION", {}



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
