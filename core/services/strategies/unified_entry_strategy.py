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

def check_streamlined_entry_signal(df, side: str, live_price: float, position_status: str, **kwargs) -> tuple[bool, str, dict]:
    """
    嚴格拆分狀態的純粹破軌開倉 (Pure Breakout Entry)：
    - 狀態 1：無倉位 (NO_POSITION) -> 首倉 (FIRST) 或 延續開倉 (CONTINUATION)
    - 狀態 2：有倉位 (OPEN) -> 加倉 (PYRAMID)
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

    # =========================================================================
    # 絕對硬防線：橫盤死水區一票否決 (Sideways Consolidation Block)
    # =========================================================================
    current_atr_val = float(current_candle.get('atr', 0))
    if current_atr_val > 0:
        # 1. 均線走平禁開（缺乏方向斜率）
        ma3_change = abs(ma3 - prev_ma3)
        min_slope_threshold = 0.15 * current_atr_val
        if ma3_change < min_slope_threshold:
            return False, f"🛑 BLOCKED_SIDEWAYS (MA3 flat: {ma3_change:.5f} < {min_slope_threshold:.5f})", {}

        # 2. 窄幅橫盤箱體禁開（K棒實體壓縮）
        # 最近 4 根已收線 K 棒 (-5 到 -2)
        if len(df) >= 6:
            recent_4_bars = df.iloc[-5:-1]
            recent_high = float(recent_4_bars['high'].max())
            recent_low = float(recent_4_bars['low'].min())
            if (recent_high - recent_low) < (0.8 * current_atr_val):
                return False, "🛑 BLOCKED_SIDEWAYS (Narrow range box)", {}

        # 3. 均線纏繞禁開
        # 最近 3 根已收線 K 棒 (-4 到 -2)
        if len(df) >= 5:
            cross_count = 0
            for i in range(-4, -1):
                b = df.iloc[i]
                b_low = float(b['low'])
                b_high = float(b['high'])
                b_ma3 = float(b.get('ma3', b.get('ema_3', 0)))
                if b_low <= b_ma3 and b_high >= b_ma3:
                    cross_count += 1
            if cross_count >= 2:
                return False, "🛑 BLOCKED_SIDEWAYS (MA3 entanglement)", {}

    if side == "LONG":
        symbol = kwargs.get('symbol', '')
        
        # ══════════════════════════════════════════════════════════════════
        # 🚨 第一層：絕對硬防線 (Absolute Hard Blocks - Live Candle)
        # 只要觸發任何一條，立即 return False，後續邏輯完全不執行
        # ══════════════════════════════════════════════════════════════════
        live_candle = df.iloc[-1]
        live_close = float(live_candle['close'])
        live_open = float(live_candle['open'])
        live_ma7 = float(live_candle.get('ma7', live_candle.get('ma5', live_candle.get('ma3', live_close))))

        # -----------------------------------------------------------------
        # 均線拐頭與斜率硬防線 (MA Slope Block) - 絕不在峰谷轉折處逆勢開單
        # -----------------------------------------------------------------
        ma_3_slope = ma3 - prev_ma3
        # 1. MA3 走平或下彎（轉彎見頂），嚴禁做多
        if ma_3_slope <= 0:
            return False, "🛑 BLOCKED_MA3_SLOPE (MA3 is flat or falling)", {}
        # 2. 當前價格跌破 MA3，禁多
        if live_close <= ma3:
            return False, "🛑 BLOCKED_MA3_PRICE (Live Close <= MA3)", {}
        
        # 1.1 收黑禁多 (Bearish Candle Block) - 使用當前未收線(Live)的報價
        if live_close < live_open:
            return False, "🛑 BLOCKED_PANIC_LONG (Live Bearish Candle, momentum lost)", {}
            
        # 1.2 短均線失守禁多 (Short MA Breach Block)
        if live_close < live_ma7 and live_ma7 > 0:
            return False, f"🛑 BLOCKED_PANIC_LONG (Live Close {live_close:.6f} < Live MA7 {live_ma7:.6f})", {}

        # 1.3 連續陽線動能衰竭禁多：連續 4 根陽線，防止天花板追多
        bullish_count = 0
        for i in range(1, min(6, len(df))):
            c = df.iloc[-i]
            if float(c['close']) > float(c['open']):
                bullish_count += 1
            else:
                break
        if bullish_count >= 4:
            return False, f"🛑 BLOCKED_PANIC_LONG (Consecutive Bullish: {bullish_count} >= 4)", {}

        # 1.4 全局極限正乖離一票否決：Bias > 2.5 ATR
        live_atr = float(live_candle.get('atr', 0))
        live_ema_base = float(live_candle.get('ema_20', live_candle.get('kc_middle', live_close)))
        if live_atr > 0 and (live_close - live_ema_base) > 2.5 * live_atr:
            return False, f"🛑 BLOCKED_PANIC_LONG (Bias > 2.5 ATR)", {}

        # 🛡️ 最終開多過濾邏輯 (Long Entry Filters) - 防止天花板追多
        # ⚠️ 延續開倉 (Continuation) 本身就需在上軌外，上軌過濾僅封鎖首倉。
        is_potential_continuation_long = (prev_close > prev_kc_upper) and (close > kc_upper)

        # 2. 衝出上軌或正乖離過大禁多 — 僅首倉適用，延續開倉豁免
        if close > kc_upper and not is_potential_continuation_long:
            return False, "BLOCKED_PANIC_LONG (Close > Upper Band, First Entry only)", {}
        atr_l = float(current_candle.get('atr', 0))
        bias_atr_l = 1.8 if "PEPE" in symbol else 1.5
        ma_7 = float(current_candle.get('ma7', current_candle.get('ema_20', current_candle.get('kc_middle', close))))
        if atr_l > 0 and (close - ma_7) > bias_atr_l * atr_l:
            return False, f"BLOCKED_PANIC_LONG (Bias > {bias_atr_l} ATR above MA7)", {}

        # 3. 末端爆發巨棒禁多 (Climax Candle — 已無後續利潤空間)
        body_len_l = abs(close - open_p)
        body_series_l = (df['close'] - df['open']).abs()
        if len(body_series_l) >= 11:
            avg_body_l = float(body_series_l.iloc[-11:-1].mean())
        else:
            avg_body_l = float(body_series_l.iloc[:-1].mean()) if len(body_series_l) > 1 else atr_l
        body_limit_l = 2.5 if "PEPE" in symbol else 2.0
        if avg_body_l > 0 and body_len_l > body_limit_l * avg_body_l:
            return False, f"BLOCKED_PANIC_LONG (Body > {body_limit_l}x AvgBody, climax candle)", {}

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


        # --- 情境 1：無倉位 (NO_POSITION) ---
        if position_status == 'NO_POSITION':
            # 子情況 1：延續開倉 (Continuation)
            if was_healthy_outside and is_healthy_outside and is_curr_bullish:
                return True, "🚀 [Continuation] LONG: 延續追車直入", {"action": "ENTER", "is_breakout": True, "entry_type": "CONTINUATION"}
                
            # 子情況 2：首倉開倉 (First Entry)
            prev_broke_kc = prev_close > prev_kc_upper
            if prev_broke_kc and is_healthy_outside and is_color_consistent_long:
                # 【防追高過濾器】首倉專屬
                kc_mid = float(current_candle.get('kc_middle', current_candle.get('ema_20', close)))
                deviation = abs(close - kc_mid) / kc_mid if kc_mid > 0 else 0
                rsi_val = float(current_candle.get('rsi', 50))
                
                import os
                max_dev = float(os.getenv("FIRST_ENTRY_MAX_DEVIATION", "0.10"))
                max_rsi = float(os.getenv("FIRST_ENTRY_MAX_RSI", "75"))
                
                if deviation > max_dev:
                    return False, f"BLOCKED_OVERHEATED (Dev: {deviation:.2%} > {max_dev:.2%})", {}
                if rsi_val > max_rsi:
                    return False, f"BLOCKED_OVERHEATED (RSI: {rsi_val:.1f} > {max_rsi})", {}
                    
                return True, "🚀 [First Entry] LONG: 破軌確認且未過熱", {"action": "ENTER", "is_breakout": True, "entry_type": "FIRST"}
                
        # --- 情境 2：有倉位 (OPEN) -> 加倉 (Pyramiding) ---
        elif position_status == 'OPEN':
            # 加倉不防追高
            if is_healthy_outside and is_curr_bullish:
                return True, "🚀 [Pyramid] LONG: 趨勢健康，加倉追進", {"action": "ENTER", "is_breakout": True, "entry_type": "PYRAMID"}

    elif side == "SHORT":
        symbol = kwargs.get('symbol', '')

        # ══════════════════════════════════════════════════════════════════
        # 🚨 第一層：絕對硬防線 (Absolute Hard Blocks - Live Candle)
        # 防止在地板大陽線追空，必須放在所有條件最前面
        # ══════════════════════════════════════════════════════════════════
        live_candle = df.iloc[-1]
        live_close = float(live_candle['close'])
        live_open = float(live_candle['open'])
        live_ma7 = float(live_candle.get('ma7', live_candle.get('ma5', live_candle.get('ma3', live_close))))

        # -----------------------------------------------------------------
        # 均線拐頭與斜率硬防線 (MA Slope Block) - 絕不在峰谷轉折處逆勢開單
        # -----------------------------------------------------------------
        ma_3_slope = ma3 - prev_ma3
        # 1. MA3 走平或翹頭（轉彎打底），嚴禁做空
        if ma_3_slope >= 0:
            return False, "🛑 BLOCKED_MA3_SLOPE (MA3 is flat or rising)", {}
        # 2. 當前價格距離 MA3 向上反撲，或前一根已出長下影線打底，禁空
        if live_close >= ma3:
            return False, "🛑 BLOCKED_MA3_PRICE (Live Close >= MA3)", {}

        # 1.1 收紅禁空 (Bullish Candle Block) - 使用當前未收線(Live)的報價
        if live_close > live_open:
            return False, "🛑 BLOCKED_PANIC_SHORT (Live Bullish candle — Close > Open, momentum on long side)", {}

        # 1.2 站上短均線禁空 (Short MA Breach Block)
        if live_close > live_ma7 and live_ma7 > 0:
            return False, f"🛑 BLOCKED_PANIC_SHORT (Live Close {live_close:.6f} > Live MA7 {live_ma7:.6f})", {}

        # 2.3 連續陰線動能衰竭禁空：連續 4 根陰線，防止地板追空
        bearish_count = 0
        for i in range(1, min(6, len(df))):
            c = df.iloc[-i]
            if float(c['close']) < float(c['open']):
                bearish_count += 1
            else:
                break
        if bearish_count >= 4:
            return False, f"🛑 BLOCKED_PANIC_SHORT (Consecutive Bearish: {bearish_count} >= 4)", {}

        # 2.4 全局極限負乖離一票否決：Bias < -2.5 ATR
        live_atr = float(live_candle.get('atr', 0))
        live_ema_base = float(live_candle.get('ema_20', live_candle.get('kc_middle', live_close)))
        if live_atr > 0 and (live_ema_base - live_close) > 2.5 * live_atr:
            return False, f"🛑 BLOCKED_PANIC_SHORT (Bias < -2.5 ATR)", {}

        # ══════════════════════════════════════════════════════════════════

        # 🛡️ 最終開空過濾邏輯 (Short Entry Filters) - 防止追殺恐慌
        # ⚠️ 延續開倉 (Continuation) 本身就需在下軌外，下軌過濾僅封鎖首倉。
        is_potential_continuation = (prev_close < prev_kc_lower) and (close < kc_lower)

        # 3. 核心過濾：極端超賣禁止 (下軌過濾) — 僅首倉適用，延續開倉豁免
        if close < kc_lower and not is_potential_continuation:
            return False, "BLOCKED_PANIC_SHORT (Close < Lower Band, First Entry only)", {}

        # 4. RSI 極端值過濾（延續開倉亦適用）
        rsi_limit = 25  # 龍蝦與 PEPE 均用 25，30 過於保守會封鎖正常下跌趨勢
        rsi_val = float(current_candle.get('rsi', 50))
        if 'rsi' in current_candle and rsi_val < rsi_limit:
            return False, f"BLOCKED_PANIC_SHORT (RSI {rsi_val:.1f} < {rsi_limit})", {}

        # 5. 乖離率限制 (Bias Limit) — 以 MA7 為錨點（延續開倉亦適用）
        ma_7 = float(current_candle.get('ma7', current_candle.get('ema_20', current_candle.get('kc_middle', close))))
        atr = float(current_candle.get('atr', 0))
        bias_atr = 1.8 if "PEPE" in symbol else 1.5
        if atr > 0 and (ma_7 - close) > bias_atr * atr:
            return False, f"BLOCKED_PANIC_SHORT (Bias > {bias_atr} ATR)", {}

        # 4. 動能過濾：恐慌棒識別 (Climax Candle Filter)（延續開倉亦適用）
        body_len = abs(close - open_p)
        body_series = (df['close'] - df['open']).abs()
        if len(body_series) >= 11:
            avg_body = float(body_series.iloc[-11:-1].mean())
        else:
            avg_body = float(body_series.iloc[:-1].mean()) if len(body_series) > 1 else atr

        body_limit = 2.5 if "PEPE" in symbol else 2.0
        if avg_body > 0 and body_len > body_limit * avg_body:
            return False, f"BLOCKED_PANIC_SHORT (Body > {body_limit}x AvgBody)", {}

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

        # --- 情境 1：無倉位 (NO_POSITION) ---
        if position_status == 'NO_POSITION':
            # 子情況 1：延續開倉 (Continuation)
            if was_healthy_outside and is_healthy_outside and is_curr_bearish:
                return True, "📉 [Continuation] SHORT: 延續追車直入", {"action": "ENTER", "is_breakout": True, "entry_type": "CONTINUATION"}
                
            # 子情況 2：首倉開倉 (First Entry)
            prev_broke_kc = prev_close < prev_kc_lower
            if prev_broke_kc and is_healthy_outside and is_color_consistent_short:
                # 【防追高/追空過濾器】首倉專屬
                kc_mid = float(current_candle.get('kc_middle', current_candle.get('ema_20', close)))
                deviation = abs(close - kc_mid) / kc_mid if kc_mid > 0 else 0
                rsi_val = float(current_candle.get('rsi', 50))
                
                import os
                max_dev = float(os.getenv("FIRST_ENTRY_MAX_DEVIATION", "0.10"))
                min_rsi = float(os.getenv("FIRST_ENTRY_MIN_RSI", "25"))
                
                if deviation > max_dev:
                    return False, f"BLOCKED_OVERHEATED (Dev: {deviation:.2%} > {max_dev:.2%})", {}
                if rsi_val < min_rsi and 'rsi' in current_candle:
                    return False, f"BLOCKED_OVERSOLD (RSI: {rsi_val:.1f} < {min_rsi})", {}
                    
                return True, "📉 [First Entry] SHORT: 破軌確認且未過熱", {"action": "ENTER", "is_breakout": True, "entry_type": "FIRST"}
                
        # --- 情境 2：有倉位 (OPEN) -> 加倉 (Pyramiding) ---
        elif position_status == 'OPEN':
            # 加倉不防追高
            if is_healthy_outside and is_curr_bearish:
                return True, "📉 [Pyramid] SHORT: 趨勢健康，加倉追進", {"action": "ENTER", "is_breakout": True, "entry_type": "PYRAMID"}

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
            existing_pos = kwargs.get("existing_pos")
            position_status = "OPEN" if existing_pos else "NO_POSITION"
            ok, reason, action_dict = check_streamlined_entry_signal(frame, side, price, position_status, **kwargs)
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
