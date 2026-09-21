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
    純粹破軌開倉 (Pure Breakout Entry)：
    - 第一步：上一根 K 線收盤破軌（觸發訊號）
    - 第二步：即時價格處於軌道外側（健康站穩）
    """
    if df is None or len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA", {}

    prev_1 = df.iloc[-2] # 剛收盤的這根
    prev_2 = df.iloc[-3]
    latest = df.iloc[-1] # 當前未收線

    c1 = float(prev_2['close'])
    o1 = float(prev_2['open'])
    c2 = float(prev_1['close'])
    o2 = float(prev_1['open'])
    
    kc_up1 = float(prev_2.get('kc_upper', 0))
    kc_up2 = float(prev_1.get('kc_upper', 0))
    kc_dn1 = float(prev_2.get('kc_lower', 0))
    kc_dn2 = float(prev_1.get('kc_lower', 0))
    
    ma3_2 = float(prev_1.get('ma3', 0))
    live_ma3 = float(latest.get('ma3', 0))
    
    kc_upper_live = float(latest.get("kc_upper", 0))
    kc_lower_live = float(latest.get("kc_lower", 0))
    
    kc_mid1 = float(prev_2.get('kc_middle', prev_2.get('ema_20', 0)))
    kc_mid2 = float(prev_1.get('kc_middle', prev_1.get('ema_20', 0)))

    if side == "LONG":
        kc_going_up = (kc_mid2 > kc_mid1)
        ma3_cross_up = (ma3_2 <= kc_up2) and (live_ma3 > kc_upper_live)
        price_outside = (live_price > kc_upper_live)
        
        cond_ma3_cross = kc_going_up and ma3_cross_up and price_outside
        cond_2_candle = (c1 > o1) and (c1 > kc_up1) and (c2 > o2) and (c2 > kc_up2) and price_outside
        cond_continuation = (c2 > kc_up2) and price_outside and (live_ma3 > ma3_2 if live_ma3 > 0 else True)
        
        if cond_ma3_cross:
            return True, "🚀 [MA3 Cross] LONG: MA3穿越KC外軌", {"action": "ENTER", "is_breakout": True}
        elif cond_2_candle:
            return True, "🚀 [2-Candle Breakout] LONG: 兩根破軌確認", {"action": "ENTER", "is_breakout": True}
        elif cond_continuation:
            return True, "🚀 [Continuation] LONG: 延續追車直入", {"action": "ENTER", "is_breakout": True}
            
    elif side == "SHORT":
        kc_going_down = (kc_mid2 < kc_mid1)
        ma3_cross_dn = (ma3_2 >= kc_dn2) and (live_ma3 < kc_lower_live)
        price_outside = (live_price < kc_lower_live)
        
        cond_ma3_cross = kc_going_down and ma3_cross_dn and price_outside
        cond_2_candle = (c1 < o1) and (c1 < kc_dn1) and (c2 < o2) and (c2 < kc_dn2) and price_outside
        cond_continuation = (c2 < kc_dn2) and price_outside and (live_ma3 < ma3_2 if live_ma3 > 0 else True)
        
        if cond_ma3_cross:
            return True, "🚀 [MA3 Cross] SHORT: MA3穿越KC外軌", {"action": "ENTER", "is_breakout": True}
        elif cond_2_candle:
            return True, "🚀 [2-Candle Breakout] SHORT: 兩根破軌確認", {"action": "ENTER", "is_breakout": True}
        elif cond_continuation:
            return True, "🚀 [Continuation] SHORT: 延續追車直入", {"action": "ENTER", "is_breakout": True}

    return False, "FILTERED_NOT_PURE_BREAKOUT", {}



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
            
        # 若為非破軌，強制回傳 WAIT，確保不會被其它可能殘留的阻擋邏輯（如 BLOCK_LONG_MACRO_WAVE_EXHAUSTED）污染。
        return False, reason, {"action": "WAIT"}
