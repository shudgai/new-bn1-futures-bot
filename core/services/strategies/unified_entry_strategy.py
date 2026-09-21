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
    雙軌進場檢驗架構 (HUNTER 版)：
    1. 尋找過去 15 根內是否有強勢突破 (第一根強勢實體穿出軌道)。
    2. 如果有突破標記，進入回調待命區。
    3. 在待命區內，只要價格回落到支撐區間 (KC上/下軌、中軌、MA15)，且目前 K 線收盤價站穩均線，立刻開倉。
    """
    if df is None or len(df) < 15:
        return False, "WAIT_INSUFFICIENT_DATA", {}

    latest = df.iloc[-1]
    prev_1 = df.iloc[-2]
    
    current_atr = float(prev_1.get('atr', 0))
    if current_atr <= 0:
        return False, "INVALID_ATR", {}

    kc_upper_live = float(latest.get("kc_upper", 0))
    kc_lower_live = float(latest.get("kc_lower", 0))
    kc_mid_live = float(latest.get("kc_middle", 0))
    ma15_live = float(latest.get('ma15', 0))
    
    # (Extreme Impulse logic will be evaluated after Retest logic as a fallback)
    latest_body = abs(float(latest['close']) - float(latest['open']))
    recent_klines = df.iloc[-4:-1] if len(df) >= 4 else df.iloc[:-1] # 取最近 3 根已收盤 K 棒
    high_3 = float(recent_klines["high"].max()) if not recent_klines.empty else 0.0
    low_3 = float(recent_klines["low"].min()) if not recent_klines.empty else 0.0
    range_3 = high_3 - low_3 if high_3 > 0 else 0.0
    is_extreme_body = (latest_body > 1.5 * current_atr) or (range_3 >= 2.0 * current_atr)

    # 1. 動態階梯記憶 (Dynamic Stair-Stepping Memory)
    # 自動更新基準高點為最近的局部破軌，且容許波段延續 (最長記憶 60 根)，除非跌穿反向軌道破壞結構。
    breakout_found = False
    breakout_bars_ago = 0
    max_lookback = min(60, len(df) - 1)
    
    if side == "LONG":
        for i in range(1, max_lookback + 1):
            p_cand = df.iloc[-(i + 1)]
            # 若途中跌破下軌 (看收盤價，容許插針洗盤)，代表多頭結構已被破壞，清空記憶
            if float(p_cand['close']) < float(p_cand.get("kc_lower", kc_lower_live)):
                break
                
            p_body = abs(float(p_cand['close']) - float(p_cand['open']))
            p_solid = p_body >= 0.15 * current_atr
            p_green = float(p_cand['close']) > float(p_cand['open'])
            p_out = float(p_cand['close']) > float(p_cand.get("kc_upper", kc_upper_live))
            
            if p_solid and p_green and p_out:
                breakout_found = True
                breakout_bars_ago = i
                break
                
    elif side == "SHORT":
        for i in range(1, max_lookback + 1):
            p_cand = df.iloc[-(i + 1)]
            # 若途中突破上軌 (看收盤價，容許插針洗盤)，代表空頭結構已被破壞，清空記憶
            if float(p_cand['close']) > float(p_cand.get("kc_upper", kc_upper_live)):
                break
                
            p_body = abs(float(p_cand['close']) - float(p_cand['open']))
            p_solid = p_body >= 0.15 * current_atr
            p_red = float(p_cand['close']) < float(p_cand['open'])
            p_out = float(p_cand['close']) < float(p_cand.get("kc_lower", kc_lower_live))
            
            if p_solid and p_red and p_out:
                breakout_found = True
                breakout_bars_ago = i
                break

    if not breakout_found:
        # 如果沒有 Breakout Memory，看是否當下直接極端爆發 (特權通道)
        if side == "LONG":
            latest_green = float(latest['close']) > float(latest['open'])
            latest_out = live_price > kc_upper_live
            if is_extreme_body and latest_green and latest_out:
                return True, "[HUNTER] Extreme Impulse Breakout (No Memory)", {"action": "ENTER"}
        elif side == "SHORT":
            latest_red = float(latest['close']) < float(latest['open'])
            latest_out = live_price < kc_lower_live
            if is_extreme_body and latest_red and latest_out:
                return True, "[HUNTER] Extreme Impulse Breakout (No Memory)", {"action": "ENTER"}
        return False, "FILTERED_NO_BREAKOUT_MEMORY", {}
        
    # 在待命模式下，檢查是否回調到紅線 (MA15) 或 KC 邊緣附近
    retest_margin = 0.8 * current_atr
    
    if side == "LONG":
        latest_low = float(latest.get('low', live_price))
        is_near_ma15 = latest_low <= (ma15_live + retest_margin)
        is_near_kc_upper = latest_low <= (kc_upper_live + retest_margin)
        is_near_kc_mid = latest_low <= (kc_mid_live + retest_margin)
        
        is_retesting = is_near_ma15 or is_near_kc_upper or is_near_kc_mid
        is_closing_above_ma15 = live_price > ma15_live
        
        # 情況一：標準回調確認 (優先)
        if is_retesting and is_closing_above_ma15:
            ma3_live = float(latest.get('ma3', 0))
            ma3_prev = float(prev_1.get('ma3', 0))
            ma15_prev = float(prev_1.get('ma15', 0))
            is_ma3_cross = (ma3_prev < ma15_prev) and (ma3_live >= ma15_live)
            
            reason = f"[HUNTER] LONG Retest after {breakout_bars_ago} bars"
            if is_ma3_cross:
                reason += " (High Momentum Cross)"
            return True, reason, {"action": "ENTER", "is_ma3_cross": is_ma3_cross}
            
        # 情況二：無回調點但極端爆發 (特權通道)
        latest_green = float(latest['close']) > float(latest['open'])
        latest_out = live_price > kc_upper_live
        if is_extreme_body and latest_green and latest_out:
            return True, "[HUNTER] Extreme Impulse Breakout (Bypass Retest)", {"action": "ENTER"}
            
        return False, f"FILTERED_WAITING_RETEST_LONG (Near support: {is_retesting}, Above MA15: {is_closing_above_ma15})", {}
            
    elif side == "SHORT":
        latest_high = float(latest.get('high', live_price))
        is_near_ma15 = latest_high >= (ma15_live - retest_margin)
        is_near_kc_lower = latest_high >= (kc_lower_live - retest_margin)
        is_near_kc_mid = latest_high >= (kc_mid_live - retest_margin)
        
        is_retesting = is_near_ma15 or is_near_kc_lower or is_near_kc_mid
        is_closing_below_ma15 = live_price < ma15_live
        
        # 情況一：標準回調確認 (優先)
        if is_retesting and is_closing_below_ma15:
            ma3_live = float(latest.get('ma3', 0))
            ma3_prev = float(prev_1.get('ma3', 0))
            ma15_prev = float(prev_1.get('ma15', 0))
            is_ma3_cross = (ma3_prev > ma15_prev) and (ma3_live <= ma15_live)
            
            reason = f"[HUNTER] SHORT Retest after {breakout_bars_ago} bars"
            if is_ma3_cross:
                reason += " (High Momentum Cross)"
            return True, reason, {"action": "ENTER", "is_ma3_cross": is_ma3_cross}
            
        # 情況二：無回調點但極端爆發 (特權通道)
        latest_red = float(latest['close']) < float(latest['open'])
        latest_out = live_price < kc_lower_live
        if is_extreme_body and latest_red and latest_out:
            return True, "[HUNTER] Extreme Impulse Breakout (Bypass Retest)", {"action": "ENTER"}
            
        return False, f"FILTERED_WAITING_RETEST_SHORT (Near support: {is_retesting}, Below MA15: {is_closing_below_ma15})", {}



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
