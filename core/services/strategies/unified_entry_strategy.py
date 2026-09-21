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
    極簡兩根破軌唯一入口：
    第一根破軌，第二根健康站上 KC 及 MA3 外，最新價在軌外。
    """
    if df is None or len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA", {}

    prev_1 = df.iloc[-2] # 第二根 (確認)
    prev_2 = df.iloc[-3] # 第一根 (破軌)
    latest = df.iloc[-1] # 當前未收線

    # 取得指標
    kc_upper_live = float(latest.get("kc_upper", 0))
    kc_lower_live = float(latest.get("kc_lower", 0))
    
    # 輔助函式：計算實體比例
    def get_kline_stats(row):
        o, c, h, l = float(row['open']), float(row['close']), float(row['high']), float(row['low'])
        body = abs(c - o)
        rng = h - l
        is_green = c > o
        is_red = c < o
        is_solid = (body / rng >= 0.2) if rng > 0 else False
        return c, is_green, is_red, is_solid, float(row.get('kc_upper', 0)), float(row.get('kc_lower', 0)), float(row.get('ma3', 0))

    c1, g1, r1, solid1, kc_up1, kc_dn1, ma3_1 = get_kline_stats(prev_2)
    c2, g2, r2, solid2, kc_up2, kc_dn2, ma3_2 = get_kline_stats(prev_1)

    if side == "LONG":
        # 1. 第一根破軌
        cond1 = g1 and solid1 and (c1 > kc_up1)
        # 2. 第二根站上 KC 外及 MA3 外
        cond2 = g2 and solid2 and (c2 > kc_up2) and (c2 > ma3_2)
        # 3. 最新價嚴格在軌外
        cond3 = live_price > kc_upper_live

        if cond1 and cond2 and cond3:
            return True, "🚀 [2-Candle Breakout] LONG: 兩根破軌確認", {"action": "ENTER"}
            
    elif side == "SHORT":
        # 1. 第一根破軌
        cond1 = r1 and solid1 and (c1 < kc_dn1)
        # 2. 第二根跌出 KC 外及 MA3 外
        cond2 = r2 and solid2 and (c2 < kc_dn2) and (c2 < ma3_2)
        # 3. 最新價嚴格在軌外
        cond3 = live_price < kc_lower_live

        if cond1 and cond2 and cond3:
            return True, "🚀 [2-Candle Breakout] SHORT: 兩根破軌確認", {"action": "ENTER"}

    return False, "FILTERED_NOT_2_CANDLE_BREAKOUT", {}



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
            
            # 計算 ATR 通膨係數 (ATR Inflation Ratio)
            try:
                current_atr = float(frame.iloc[-2].get('atr', 0))
                past_120 = frame['atr'].tail(120)
                avg_atr = float(past_120.mean()) if not past_120.empty else current_atr
                atr_inflation = (current_atr / avg_atr) if avg_atr > 0 else 1.0
                base_dict["atr_inflation"] = atr_inflation
            except Exception:
                base_dict["atr_inflation"] = 1.0
                
            return True, reason, base_dict
        return False, reason, {"action": "WAIT"}
