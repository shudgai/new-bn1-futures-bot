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
    嚴格拆分狀態的純粹破軌開倉 (Pure Breakout Entry)：
    - 狀態 1：首倉開倉 (First Entry) = 前一收盤破軌 + 最新收盤站穩 (KC外側且MA3外側)
    - 狀態 2：延續開倉 (Continuation) = 前一收盤站穩 + 最新收盤站穩
    這裡使用已收線 (df.iloc[-2]) 作為判斷基準，避免未收線跳動假訊號。
    """
    if df is None or len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA", {}

    # 取已收線的 K 線數據
    # df.iloc[-1] 是未收線(Live)，iloc[-2] 是剛收線(Current Confirmed)，iloc[-3] 是前一根收線(Prev Confirmed)
    current_candle = df.iloc[-2]
    prev_candle = df.iloc[-3]

    close = float(current_candle['close'])
    open_p = float(current_candle['open'])
    kc_upper = float(current_candle.get('kc_upper', 0))
    kc_lower = float(current_candle.get('kc_lower', 0))
    ma3 = float(current_candle.get('ma3', current_candle.get('ema_3', 0)))

    prev_close = float(prev_candle['close'])
    prev_open = float(prev_candle['open'])
    prev_kc_upper = float(prev_candle.get('kc_upper', 0))
    prev_kc_lower = float(prev_candle.get('kc_lower', 0))
    prev_ma3 = float(prev_candle.get('ma3', prev_candle.get('ema_3', 0)))

    if side == "LONG":
        # 1. 判斷 K 線顏色 (陽燭)
        is_curr_bullish = close > open_p
        is_prev_bullish = prev_close > prev_open
        is_color_consistent_long = is_curr_bullish and is_prev_bullish
        
        # 2. 定義「健康站在外側」
        is_outside_kc = close > kc_upper
        is_above_ma3 = close > ma3
        is_healthy_outside = is_outside_kc and is_above_ma3
        
        was_outside_kc = prev_close > prev_kc_upper
        was_above_ma3 = prev_close > prev_ma3
        was_healthy_outside = was_outside_kc and was_above_ma3

        # 子情況 1：延續開倉 (Continuation)
        # 條件：前一根已收線已經在外側，且當前已收線仍在健康外側，並且當前是陽燭
        if was_healthy_outside and is_healthy_outside and is_curr_bullish:
            return True, "🚀 [Continuation] LONG: 延續追車直入", {"action": "ENTER", "is_breakout": True}
            
        # 子情況 2：首倉開倉 (First Entry)
        # 條件：前一根「突破」了 KC (Close > KC_Upper)，當前「確認」站在 KC 及 MA3 外側，且兩根顏色皆為陽燭
        prev_broke_kc = prev_close > prev_kc_upper
        if prev_broke_kc and is_healthy_outside and is_color_consistent_long:
            return True, "🚀 [First Entry] LONG: 兩根同色破軌確認", {"action": "ENTER", "is_breakout": True}

    elif side == "SHORT":
        # 1. 判斷 K 線顏色 (陰燭)
        is_curr_bearish = close < open_p
        is_prev_bearish = prev_close < prev_open
        is_color_consistent_short = is_curr_bearish and is_prev_bearish
        
        # 2. 定義「健康站在外側」
        is_outside_kc = close < kc_lower
        is_below_ma3 = close < ma3
        is_healthy_outside = is_outside_kc and is_below_ma3
        
        was_outside_kc = prev_close < prev_kc_lower
        was_below_ma3 = prev_close < prev_ma3
        was_healthy_outside = was_outside_kc and was_below_ma3

        # 子情況 1：延續開倉 (Continuation)
        # 條件：前一根已收線已經在外側，且當前已收線仍在健康外側，並且當前是陰燭
        if was_healthy_outside and is_healthy_outside and is_curr_bearish:
            return True, "🚀 [Continuation] SHORT: 延續追車直入", {"action": "ENTER", "is_breakout": True}
            
        # 子情況 2：首倉開倉 (First Entry)
        # 條件：前一根「突破」了 KC，當前「確認」站在外側，且兩根皆為陰燭
        prev_broke_kc = prev_close < prev_kc_lower
        if prev_broke_kc and is_healthy_outside and is_color_consistent_short:
            return True, "🚀 [First Entry] SHORT: 兩根同色破軌確認", {"action": "ENTER", "is_breakout": True}

    return False, "FILTERED_NOT_PURE_BREAKOUT", {}



class UnifiedEntryStrategy(IEntryStrategy):
    def evaluate_entry(self, frame, price, side, **kwargs):
        if frame is None or len(frame) < 10 or ("kc_middle" not in frame.columns and "ema_20" not in frame.columns):
            return False, "WAIT_INSUFFICIENT_DATA_OR_INDICATORS", {"action": "WAIT"}

        # --- 處理 COOLDOWN 狀態 (量能衰竭平倉後的冷卻期) ---
        meta = kwargs.get("meta", {})
        if meta.get("cooldown_mode") == "WAIT_FOR_VOLUME_RECOVERY":
            try:
                import core.config as config
                current_volume = float(frame['volume'].iloc[-2]) # 使用剛收線的 K 線判斷
                
                avg_period = getattr(config, 'VOLUME_WEAKNESS_AVG_PERIOD', 20)
                recovery_thresh = getattr(config, 'VOLUME_RECOVERY_THRESHOLD', 1.2)
                
                if len(frame) > avg_period + 1:
                    vol_slice = frame['volume'].iloc[-(avg_period + 2):-2]
                    volume_avg = float(vol_slice.mean())
                else:
                    volume_avg = float(frame['volume'].iloc[:-2].mean())
                    
                is_volume_strong = current_volume > (volume_avg * recovery_thresh)
                
                close = float(frame['close'].iloc[-2])
                kc_upper = float(frame.get('kc_upper', frame).iloc[-2])
                kc_lower = float(frame.get('kc_lower', frame).iloc[-2])
                is_outside = (close > kc_upper) if side == "LONG" else (close < kc_lower)
                
                if is_outside and is_volume_strong:
                    meta["cooldown_mode"] = "NONE"  # 解除冷卻
                    # 允許後續繼續評估開倉
                else:
                    return False, f"WAIT_VOLUME_RECOVERY (Vol={current_volume:.2f}, Avg={volume_avg:.2f})", {"action": "WAIT"}
            except Exception as e:
                return False, f"WAIT_VOLUME_RECOVERY_ERROR_{e}", {"action": "WAIT"}

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
