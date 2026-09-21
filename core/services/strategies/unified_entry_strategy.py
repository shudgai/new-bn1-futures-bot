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
    雙軌並行進場入口：
    1. 常規兩根破軌：第一根破軌，第二根健康站上 KC 及 MA3 外，最新價在軌外。
    2. 極端單根直入：第一根實體大於 1.5 ATR 且破軌，最新價在軌外，免等第二根確認。
    """
    if df is None or len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA", {}

    prev_1 = df.iloc[-2] # 剛收盤的這根
    prev_2 = df.iloc[-3] # 前一根
    latest = df.iloc[-1] # 當前未收線

    # 取得指標
    current_atr = float(prev_1.get('atr', 0))
    if current_atr <= 0:
        return False, "INVALID_ATR", {}

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
        return c, is_green, is_red, is_solid, float(row.get('kc_upper', 0)), float(row.get('kc_lower', 0)), float(row.get('ma3', 0)), body

    c1, g1, r1, solid1, kc_up1, kc_dn1, ma3_1, body1 = get_kline_stats(prev_2)
    c2, g2, r2, solid2, kc_up2, kc_dn2, ma3_2, body2 = get_kline_stats(prev_1)

    # 判定 prev_1 (剛收盤的那根) 是否為極端動能 K 線 (實體 >= 1.0 ATR)
    is_prev1_extreme = body2 >= 1.0 * current_atr
            
    live_ma3 = float(latest.get("ma3", 0))

    if side == "LONG":
        # 常規：兩根破軌 (第一根破軌, 第二根站上 KC 外及 MA3 外)
        cond_regular = (g1 and solid1 and c1 > kc_up1) and (g2 and solid2 and c2 > kc_up2 and c2 > ma3_2)
        # 極端：單根大爆發 (剛收盤的這根大於 1.5 ATR 且破軌)
        cond_extreme = (g2 and solid2 and c2 > kc_up2) and is_prev1_extreme
        # 追車/延續：價格在軌外，且即時 MA3 正在向上移動 (允許 MA3 些微滯後在軌內)
        cond_continuation = (live_price > kc_upper_live) and (live_ma3 > ma3_2)

        if cond_regular or cond_extreme or cond_continuation:
            reason = (
                "🚀 [1-Candle Extreme] LONG: 極端爆發直入" if cond_extreme else
                "🚀 [2-Candle Breakout] LONG: 兩根破軌確認" if cond_regular else
                "🚀 [Continuation] LONG: 延續追車直入 (MA3向上)"
            )
            return True, reason, {"action": "ENTER"}
            
    elif side == "SHORT":
        # 常規：兩根破軌 (第一根破軌, 第二根跌出 KC 外及 MA3 外)
        cond_regular = (r1 and solid1 and c1 < kc_dn1) and (r2 and solid2 and c2 < kc_dn2 and c2 < ma3_2)
        # 極端：單根大瀑布 (剛收盤的這根大於 1.5 ATR 且破軌)
        cond_extreme = (r2 and solid2 and c2 < kc_dn2) and is_prev1_extreme
        # 追車/延續：價格在軌外，且即時 MA3 正在向下移動 (允許 MA3 些微滯後在軌內)
        cond_continuation = (live_price < kc_lower_live) and (live_ma3 < ma3_2)

        if cond_regular or cond_extreme or cond_continuation:
            reason = (
                "🚀 [1-Candle Extreme] SHORT: 極端瀑布直入" if cond_extreme else
                "🚀 [2-Candle Breakout] SHORT: 兩根破軌確認" if cond_regular else
                "🚀 [Continuation] SHORT: 延續追車直入"
            )
            return True, reason, {"action": "ENTER"}

    return False, "FILTERED_NOT_2_CANDLE_BREAKOUT_OR_CONTINUATION", {}



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
