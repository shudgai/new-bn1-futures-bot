import math
from core.services.candle_data import closed_entry_candles, closed_entry_problem




from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy






def check_ma_cross_entry(df):
    """【條件 A：均線金叉/死叉型】"""
    from core.services.candle_data import closed_entry_candles
    df_closed = closed_entry_candles(df)
    
    if len(df_closed) < 2:
        return None

    c1 = df_closed.iloc[-2]  # 前一根已收盤
    c2 = df_closed.iloc[-1]  # 最新已收盤
    atr = float(c2.get("atr", 0))

    if not math.isfinite(atr) or atr <= 0:
        return None

    c1_ma3, c1_ma15 = float(c1["ma3"]), float(c1["ma15"])
    c2_ma3, c2_ma15 = float(c2["ma3"]), float(c2["ma15"])
    c2_kc_middle = float(c2["kc_middle"])
    c2_close = float(c2["close"])

    # 【條件 C：大陽/大陰反轉進場規則 (Engulfing Reversal)】
    c1_open, c1_close = float(c1["open"]), float(c1["close"])
    c2_open = float(c2["open"])
    c2_body = abs(c2_close - c2_open)
    is_big_candle = c2_body > 1.2 * atr
    
    # 1. 多單判斷
    golden_cross = (c1_ma3 <= c1_ma15) and (c2_ma3 > c2_ma15)
    golden_condition = golden_cross and (c2_close > float(c2["open"])) and (c2_close > c2_kc_middle)
    
    c1_is_red_or_small_green = (c1_close < c1_open) or (abs(c1_close - c1_open) <= 0.5 * atr)
    bullish_engulfing = (
        is_big_candle 
        and (c2_close > c2_open) 
        and c1_is_red_or_small_green
        and (c2_close > max(c1_open, c1_close))
    )

    if golden_condition or bullish_engulfing:
        return {
            "action": "ENTER",
            "side": "LONG",
            "reason": "MA_CROSS_OR_ENGULFING_LONG",
            "entry_atr": atr,
            "bypass_flat_check": True,
            "bypass_cooldown": True
        }

    # 2. 空單判斷
    death_cross = (c1_ma3 >= c1_ma15) and (c2_ma3 < c2_ma15)
    death_condition = death_cross and (c2_close < float(c2["open"])) and (c2_close < c2_kc_middle)
    
    c1_is_green_or_small_red = (c1_close > c1_open) or (abs(c1_close - c1_open) <= 0.5 * atr)
    bearish_engulfing = (
        is_big_candle 
        and (c2_close < c2_open) 
        and c1_is_green_or_small_red
        and (c2_close < min(c1_open, c1_close))
    )

    if death_condition or bearish_engulfing:
        return {
            "action": "ENTER",
            "side": "SHORT",
            "reason": "MA_CROSS_OR_ENGULFING_SHORT",
            "entry_atr": atr,
            "bypass_flat_check": True,
            "bypass_cooldown": True
        }

    return None


def check_streamlined_entry_signal(df, side: str, live_price: float, position_status: str, **kwargs) -> tuple[bool, str, dict]:
    """
    嚴格拆分狀態的純粹破軌開倉 (Pure Breakout Entry)：
    - 狀態 1：無倉位 (NO_POSITION) -> 首倉 (FIRST) 或 延續開倉 (CONTINUATION)
    - 狀態 2：有倉位 (OPEN) -> 加倉 (PYRAMID)
    這裡使用已收線 (df.iloc[-2]) 作為判斷基準，避免未收線跳動假訊號。
    """
    from core.services.candle_data import closed_entry_candles
    df_closed = closed_entry_candles(df) if df is not None else None
    if df_closed is None or len(df_closed) < 5:
        return False, "WAIT_INSUFFICIENT_DATA", {}

    # Reject incomplete or nonfinite snapshots before any entry can bypass guards.
    if side not in ("LONG", "SHORT"):
        return False, "INVALID_SIDE", {}
    problem = closed_entry_problem(df_closed)
    if problem:
        return False, problem, {}
    try:
        indicators = df_closed.iloc[-2:][["ma3", "ma15", "kc_middle"]].astype(float)
        if not all(math.isfinite(v) and v > 0 for v in indicators.to_numpy().flat):
            return False, "WAIT_INVALID_MARKET_DATA", {}
    except (KeyError, TypeError, ValueError, OverflowError):
        return False, "WAIT_INVALID_MARKET_DATA", {}

    # 檢查 MA3 / MA15 交叉開倉 (具有最高優先權)
    ma_cross_signal = check_ma_cross_entry(df_closed)
    if ma_cross_signal and ma_cross_signal.get("side") == side:
        return True, f"🚀 [MA Cross] {side}: 均線交叉動能確認", ma_cross_signal

    # 取已收線的 K 線數據 (徹底屏蔽未收線的 Tick)
    current_candle = df_closed.iloc[-1]
    prev_candle = df_closed.iloc[-2]

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
    # 依使用者最新要求，已全面解除 KC 走平與橫盤阻擋！
    # =========================================================================
    current_atr_val = float(current_candle.get('atr', 0))


    if side == "LONG":
        symbol = kwargs.get('symbol', '')
        
        # ══════════════════════════════════════════════════════════════════
        # 🚨 第一層：絕對硬防線 (Absolute Hard Blocks - Live Candle)
        # 只要觸發任何一條，立即 return False，後續邏輯完全不執行
        # ══════════════════════════════════════════════════════════════════
        live_candle = current_candle
        live_close = close
        live_open = open_p
        live_ma7 = float(live_candle.get('ma7', live_candle.get('ma5', live_candle.get('ma3', live_close))))
        live_atr = current_atr_val

        # =========================================================================
        # 動態波段動能竭盡硬防線 (ATR-Based Wave Extension Block)
        # ★ 修正：門檻 4.5→6.0 ATR，且已在 KC 上軌外時豁免（外軌突破入口不能被誤殺）
        # =========================================================================
        if len(df) >= 30 and live_atr > 0:
            lookback_bars = 30
            wave_low = float(df['low'].iloc[-lookback_bars:].min())
            max_extension_atr = 6.0 * live_atr
            accumulated_rise = live_close - wave_low
            kc_upper_val = float(live_candle.get('kc_upper', 0))
            already_outside_upper = kc_upper_val > 0 and live_close >= kc_upper_val
            if accumulated_rise >= max_extension_atr and not already_outside_upper:
                return False, f"🛑 BLOCKED_WAVE_EXHAUSTED (Rise {accumulated_rise:.4f} >= {max_extension_atr:.4f})", {}

        # -----------------------------------------------------------------
        # 均線拐頭與斜率硬防線 (MA Slope Block) - 絕不在峰谷轉折處逆勢開單
        # -----------------------------------------------------------------
        ma_3_slope = ma3 - prev_ma3
        # 1. MA3 走平或下彎（轉彎作頭），嚴禁做多
        if ma_3_slope <= 0:
            return False, "🛑 BLOCKED_MA3_SLOPE (MA3 is flat or falling)", {}
        # 2. 當前價格跌破 MA3，禁多
        if live_close <= ma3:
            return False, "🛑 BLOCKED_MA3_PRICE (Live Close <= MA3)", {}
        
        # 1.1 收綠禁多 (Bearish Candle Block) - 使用當前未收線(Live)的報價，並嚴格要求上一根已收盤K棒必須為陽線
        prev_close = float(df.iloc[-2]['close'])
        prev_open = float(df.iloc[-2]['open'])
        if prev_close <= prev_open:
            return False, "🛑 BLOCKED_PANIC_LONG (Previous closed bar is Bearish or Doji)", {}
        if live_close < live_open:
            return False, "🛑 BLOCKED_PANIC_LONG (Live Bearish candle — Close < Open, momentum on short side)", {}
            
        # 1.2 短均線失守禁多 (Short MA Breach Block)
        if live_close < live_ma7 and live_ma7 > 0:
            return False, f"🛑 BLOCKED_PANIC_LONG (Live Close {live_close:.6f} < Live MA7 {live_ma7:.6f})", {}

        # 1.3 連續陽線動能衰竭禁多：連續 6 根陽線，防止天花板追多
        bullish_count = 0
        for i in range(1, min(8, len(df))):
            c = df.iloc[-i]
            if float(c['close']) > float(c['open']):
                bullish_count += 1
            else:
                break
        if bullish_count >= 6:
            return False, f"🛑 BLOCKED_PANIC_LONG (Consecutive Bullish: {bullish_count} >= 6)", {}

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
        live_candle = current_candle
        live_close = close
        live_open = open_p
        live_ma7 = float(live_candle.get('ma7', live_candle.get('ma5', live_candle.get('ma3', live_close))))
        live_atr = current_atr_val

        # =========================================================================
        # 動態波段動能竭盡硬防線 (ATR-Based Wave Extension Block)
        # ★ 同步修正：門檻 4.5→6.0 ATR，已在 KC 下軌外時豁免殺短
        # =========================================================================
        if len(df) >= 30 and live_atr > 0:
            lookback_bars = 30
            wave_high = float(df['high'].iloc[-lookback_bars:].max())
            max_extension_atr = 6.0 * live_atr
            accumulated_drop = wave_high - live_close
            kc_lower_val = float(live_candle.get('kc_lower', float('inf')))
            already_outside_lower = kc_lower_val < float('inf') and live_close <= kc_lower_val
            if accumulated_drop >= max_extension_atr and not already_outside_lower:
                return False, f"🛑 BLOCKED_WAVE_EXHAUSTED (Drop {accumulated_drop:.4f} >= {max_extension_atr:.4f})", {}

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

        # 1.1 收紅禁空 (Bullish Candle Block) - 使用當前未收線(Live)的報價，並嚴格要求上一根已收盤K棒必須為陰線
        prev_close = float(df.iloc[-2]['close'])
        prev_open = float(df.iloc[-2]['open'])
        if prev_close >= prev_open:
            return False, "🛑 BLOCKED_PANIC_SHORT (Previous closed bar is Bullish or Doji)", {}
        if live_close > live_open:
            return False, "🛑 BLOCKED_PANIC_SHORT (Live Bullish candle — Close > Open, momentum on long side)", {}

        # 1.2 站上短均線禁空 (Short MA Breach Block)
        if live_close > live_ma7 and live_ma7 > 0:
            return False, f"🛑 BLOCKED_PANIC_SHORT (Live Close {live_close:.6f} > Live MA7 {live_ma7:.6f})", {}

        # 2.3 連續陰線動能衰竭禁空：連續 6 根陰線，防止地板追空
        bearish_count = 0
        for i in range(1, min(8, len(df))):
            c = df.iloc[-i]
            if float(c['close']) < float(c['open']):
                bearish_count += 1
            else:
                break
        if bearish_count >= 6:
            return False, f"🛑 BLOCKED_PANIC_SHORT (Consecutive Bearish: {bearish_count} >= 6)", {}

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
        # 依使用者要求：如果已經強勢破底 (close < kc_lower)，百分之百放行，不阻擋
        is_strong_breakout_short = (close < kc_lower)
        
        if not is_strong_breakout_short:
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
        from core.services.outer_turn_entry import observation_store
        from core.services.closed_breakout_entry import evaluate_channel_entry, close_identity, clear_pullback
        engine = kwargs.get('engine')
        observations = observation_store(engine) if engine is not None else kwargs.get('observations')
        if kwargs.get('existing_pos'):
            if observations is not None:
                observations.pop((kwargs.get('symbol', ''), side), None)
                clear_pullback(observations, kwargs.get('symbol', ''), side)
            return False, 'WAIT_EXISTING_POSITION', {'action': 'WAIT'}
        return evaluate_channel_entry(frame, price, side, observations,
                                   kwargs.get('symbol', ''), kwargs.get('now'),
                                   close_identity(engine, kwargs.get('symbol', '')))
