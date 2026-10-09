"""Evaluate and score standard strategy signals."""

from typing import Any

import pandas as pd


def evaluate_signal(
    strategy: Any,
    rules: Any,
    df: pd.DataFrame,
    ema_50_1h: float | None = None,
    trend_1h_declining: bool = False,
    st_direction_1h: int | None = None,
    btc_st_direction_1h: int = 0,
    btc_st_flip_age: int = 999,
    symbol: str | None = None,
    parameter_overrides: dict | None = None,
    indicators_precomputed: bool = False,
) -> dict:
    if len(df) < 5:
        return {
            "action": "HOLD", "reason": "Not enough data",
            "eligible": False, "score_stage": "ELIGIBILITY",
        }
    if not indicators_precomputed:
        df = strategy.compute_indicators(df)

    # (已停用) 原本的「強勢多單訊號（綠K衝外軌）」會導致在上軌追多，
    # 違背了使用者「多單要在下軌買，空單要在上軌買」的核心邏輯，因此全面移除。

    curr = df.iloc[-1]

    live_price = float(curr['close'])

    # User Rule: 開倉都要在突破上下軌才開倉，其他位子不要開倉
    if live_price <= curr['kc_upper'] and live_price >= curr['kc_lower']:
        return {
            "action": "HOLD",
            "reason": "User Rule: 價格未突破上下軌，不允許開倉",
            "eligible": False,
            "score_stage": "ELIGIBILITY"
        }

    sig = rules.detect_simple_ma5_signal(df, live_price=live_price)

    if sig.get("detected"):
        side = sig["side"]
        rsi = float(curr.get("rsi", 50.0))
        volume_ratio = float(curr.get("volume", 0) / curr.get("vol_ma_20", 1)) if curr.get("vol_ma_20") else 1.0

        is_valid_kc = False
        # 必須是這波轉折的K線(近7根內)真正碰到極端軌道，才算有效轉向
        if side == "LONG":
            is_valid_kc = any(float(df.iloc[-i]['low']) <= float(df.iloc[-i]['kc_lower']) for i in range(1, 8) if i <= len(df))
        else:
            is_valid_kc = any(float(df.iloc[-i]['high']) >= float(df.iloc[-i]['kc_upper']) for i in range(1, 8) if i <= len(df))

        if not is_valid_kc:
            return {
                "action": "HOLD",
                "reason": "MA3 出現轉折，但近7根轉折點並未觸及 KC 通道極端值，避免盤整假突破",
                "eligible": False,
                "score_stage": "ELIGIBILITY",
            }

        return {
            "action": "ENTER_MARKET",
            "side": side,
            "reason": sig["reason"] + " (確認觸及KC邊界)",
            "price": sig["price"],
            "score": 100,
            "eligible": True,
            "score_stage": "FINAL",
            "profit_profile": "TREND_EXTENSION",
            "entry_mode": "MA3_PIVOT",
            "fast_entry": True,
            "diagnostics": {
                "price": sig["price"],
                "atr": float(curr.get("atr", 0.0)),
                "atr_pct": float(curr.get("atr", 0.0) / curr['close']) if curr['close'] > 0 else 0.0,
                "rsi": rsi,
                "adx": float(curr.get("adx", 0.0)) if not rules.pd.isna(curr.get("adx")) else 0.0,
                "volume_ratio": volume_ratio,
                "candle_pattern": "None",
            }
        }
    prev = df.iloc[-2] if len(df) >= 2 else None

    from core.indicators import analyze_candle_pattern
    candle_pattern = analyze_candle_pattern(curr)
    pattern_name = candle_pattern.get("pattern_name", "None")

    overrides = dict(parameter_overrides or {})
    volume_min_ratio = float(overrides.get("volume_min_ratio", rules.KELTNER_MIN_VOLUME_RATIO))
    volume_ratio = float(curr["volume"] / curr["vol_ma_20"]) if float(curr["vol_ma_20"]) > 0 else 0.0
    st_dir = int(curr['st_direction'])
    quality_gate = rules.evaluate_entry_quality_gate(
        side="LONG" if st_dir == 1 else "SHORT",
        price=float(curr['close']),
        atr=float(curr['atr']),
        volume_ratio=volume_ratio,
        score=85,
        df=df,
        min_volume_ratio=volume_min_ratio,
    )
    if quality_gate["blocked"]:
        return {
            "action": "HOLD",
            "reason": quality_gate["reason"],
            "eligible": False,
            "score_stage": "ELIGIBILITY",
            "diagnostics": {
                "price": float(curr['close']),
                "atr": float(curr['atr']),
                "atr_pct": float(curr['atr'] / curr['close']) if float(curr['close']) > 0 else 0.0,
                "rsi": float(curr['rsi']),
                "adx": float(curr['adx']) if not rules.pd.isna(curr['adx']) else 0.0,
                "volume_ratio": volume_ratio,
                "candle_pattern": pattern_name,
            },
        }
    atr_min_pct = float(overrides.get("atr_min_pct", rules.MIN_ATR_PCT))
    rsi_long_max = float(overrides.get("rsi_long_max", rules.RSI_LONG_MAX))
    rsi_short_min = float(overrides.get("rsi_short_min", rules.RSI_SHORT_MIN))
    btc_score_penalty = overrides.get("btc_score_penalty")

    # --- 基本數據提取 ---
    price = curr['close_price_spike_filtered'] if ('close_price_spike_filtered' in curr and not rules.pd.isna(curr['close_price_spike_filtered'])) else curr['close']
    atr = curr['atr'] if not rules.np.isnan(curr['atr']) else price * 0.015
    rsi = curr['rsi']
    adx = curr['adx'] if not rules.np.isnan(curr['adx']) else 0.0
    vol = curr['volume']
    vol_ma_20 = curr['vol_ma_20'] if not rules.np.isnan(curr['vol_ma_20']) else 0
    kc_upper = curr['kc_upper']
    kc_lower = curr['kc_lower']
    kc_width = curr['kc_width'] if not rules.np.isnan(curr['kc_width']) else (price * 0.03)
    ema_20 = curr['ema_20'] if not rules.np.isnan(curr['ema_20']) else price
    ema_50 = curr['ema_50'] if not rules.np.isnan(curr['ema_50']) else price

    def signal_diagnostics() -> dict:
        return {
            "price": float(price),
            "atr": float(atr),
            "atr_pct": float(atr / price) if price > 0 else 0.0,
            "rsi": float(rsi),
            "adx": float(adx),
            "volume_ratio": float(vol / vol_ma_20) if vol_ma_20 > 0 else 0.0,
            "ema_20": float(ema_20),
            "ema_50_1h": float(ema_50_1h) if ema_50_1h is not None else None,
            "st_direction_5m": int(st_dir),
            "st_direction_1h": int(st_direction_1h) if st_direction_1h is not None else None,
            "btc_direction_1h": int(btc_st_direction_1h or 0),
            "btc_flip_age": int(btc_st_flip_age),
            "candle_pattern": pattern_name,
        }

    def eligibility_hold(reason: str) -> dict:
        return {
            "action": "HOLD", "reason": reason,
            "eligible": False, "score_stage": "ELIGIBILITY",
            "diagnostics": signal_diagnostics(),
        }

    # --- 1. 底線防禦 (Mandatory Filters) ---

    st_dir = curr['st_direction']

    # 計算支撐與壓力區（基於最近 24 根已收盤的 5m K棒）
    # PRICE_NEAR_SUPPORT_PCT：做多要求現價在支撐位 N% 以內；做空要求現價在壓力位 N% 以內。
    # 設為 0（或負值）則完全停用此條件。預設 8%，比原本 3% 寬鬆，避免趨勢延伸時永遠進不了場。
    _near_pct = float(getattr(rules._core_config, 'PRICE_NEAR_SUPPORT_PCT', rules.PRICE_NEAR_SUPPORT_PCT))
    if _near_pct > 0 and symbol is not None and 'low' in df.columns and 'high' in df.columns and len(df) >= 25:
        past_24_bars = df.iloc[-25:-1]
        support_level = float(past_24_bars['low'].min())
        resistance_level = float(past_24_bars['high'].max())

        # 做多：必須在支撐位 N% 內
        if st_dir == 1 and price > support_level * (1.0 + _near_pct):
            return eligibility_hold(
                f"Mandatory_Fail: Price_Not_Near_Support({price:.6g}>{support_level:.6g}*{1.0+_near_pct:.3f})"
            )

        # 做空：必須在壓力位 N% 內
        if st_dir == -1 and price < resistance_level * (1.0 - _near_pct):
            return eligibility_hold(
                f"Mandatory_Fail: Price_Not_Near_Resistance({price:.6g}<{resistance_level:.6g}*{1.0-_near_pct:.3f})"
            )

    # 層 A：BTC 大盤風險調整。剛翻轉仍暫停；方向相反改為扣分與縮倉，
    # 讓真正相對強勢的個幣仍可在通過其餘品質與回踩確認後進場。
    btc_regime = rules.classify_btc_regime(
        st_dir, btc_st_direction_1h, btc_st_flip_age, symbol=symbol,
        score_penalty=btc_score_penalty,
    )
    if btc_regime["hard_block"]:
        block_reason = (
            "BTC_1h_ST_Contrary"
            if btc_regime["mode"] == "CONTRARY"
            else f"BTC_1h_ST_JustFlipped({btc_st_flip_age}bars<{rules.BTC_REGIME_FLIP_BUFFER_BARS})"
        )
        return eligibility_hold(f"Mandatory_Fail: {block_reason}")

    # 層 B：個幣自身 1h SuperTrend 方向對齊
    # 1h SuperTrend 翻轉需要較長時間，比 price vs EMA50 準確 3~5 倍。
    # 既然進場是以大方向作為風控，當 1h ST 與 5m 顯示相反方向時，
    # 需直接擋單，避免在高週期逆勢中執行 5m MomentumCross 追價。
    if st_direction_1h is not None and int(st_direction_1h) != 0 and st_dir != int(st_direction_1h):
        return eligibility_hold(
            f"Mandatory_Fail: MomentumCross_Not_Aligned(5m={st_dir},1h={int(st_direction_1h)})"
        )

    # 近期高/低點附近不允許直接反手開倉：若價格仍站在最近極值附近，
    # 但 MACD 仍顯示相反方向背離，直接拒絕開單，避免大幅反向追單。
    if st_dir == 1:
        if "high" in df.columns and len(df) >= 25:
            recent_high = float(df.iloc[-25:-1]["high"].max())
            recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
        elif "high" in df.columns:
            recent_high = float(df["high"].max())
            recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
        else:
            recent_high_distance_pct = 1.0
        bearish_divergence = False
        if "macd_hist" in df.columns and "close" in df.columns:
            bearish_divergence = rules.detect_macd_divergence(df, "SHORT")
        if recent_high_distance_pct <= 0.015 and bearish_divergence:
            # 不再做硬性擋單，改為在分數/診斷中標記為品質下降，讓 scoring 決定是否開倉
            # 將理由加入診斷以便日誌觀察
            curr_reason = f"Bearish_MACD_Divergence_Near_Recent_High(距離前高{recent_high_distance_pct:.2%}, MACD 背離顯示空頭仍強，拒絕做多)"
            # attach to diagnostics via an ad-hoc key
            curr = curr.copy()
            curr['eligibility_note'] = curr_reason
    elif st_dir == -1:
        if "high" in df.columns and len(df) >= 25:
            recent_high = float(df.iloc[-25:-1]["high"].max())
            recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
        elif "high" in df.columns:
            recent_high = float(df["high"].max())
            recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
        else:
            recent_high_distance_pct = 1.0
        recent_peak_near = recent_high_distance_pct <= 0.015
        prev_close = float(prev.get("close", price)) if prev is not None and not rules.pd.isna(prev.get("close", price)) else price
        close_broke_prev = float(curr.get("close", price)) < prev_close
        reversal_confirmed = bool(
            close_broke_prev
            and float(curr.get("close", price)) < float(curr.get("open", price))
            and (
                float(curr.get("rsi", 50.0)) < 50.0
                or float(curr.get("macd_hist", 0.0)) < 0.0
            )
        )
        if recent_peak_near and not reversal_confirmed:
            curr_reason = f"Near_Recent_High_No_Reversal_Confirmation(距離前高{recent_high_distance_pct:.2%}, 近期高點附近須先跌破前一根收盤與確認反轉)"
            curr = curr.copy()
            curr['eligibility_note'] = curr_reason
        if "low" in df.columns and len(df) >= 25:
            recent_low = float(df.iloc[-25:-1]["low"].min())
        elif "low" in df.columns:
            recent_low = float(df["low"].min())
        else:
            recent_low = price
        recent_low_distance_pct = abs(price - recent_low) / max(abs(recent_low), 1e-12)
        bullish_divergence = False
        if "macd_hist" in df.columns and "close" in df.columns:
            bullish_divergence = rules.detect_macd_divergence(df, "LONG")
        if recent_low_distance_pct <= 0.015 and bullish_divergence:
            curr_reason = f"Bullish_MACD_Divergence_Near_Recent_Low(距離近期低點{recent_low_distance_pct:.2%}, MACD 背離顯示多頭仍可反彈，拒絕空單)"
            curr = curr.copy()
            curr['eligibility_note'] = curr_reason

    # 層 C：1h EMA50 輔助確認（第三道防線）
    # 1h SuperTrend 覆蓋不到的邊緣情況（如剛翻轉尚未展開），
    # 這一層確保價格需明顯站穩 EMA50 同側才允許。
    # 1h EMA50 方向檢查已禁用，允許逆勢進場

    # 防線 3：ADX 硬性最低門檻 — ADX 低於此值代表市場完全無趨勢動能，
    # 盤整期假突破發生率最高，直接 HOLD 不進入評分系統。
    # 注意：ADX 衰退擋單（D2，見下方）是另一種機制，兩者互補不衝突：
    # 這裡擋「太低」，D2 擋「方向沒變但動能在退潮」。
    adx_floor, strong_trend = rules.get_dynamic_adx_floor(df, st_dir)
    # --- 使用者要求：解除 ADX 限制，不再因為 ADX 過低而阻擋開倉 ---
    # if adx < adx_floor:
    #     adx_mode = "Strong_Trend" if strong_trend else "Range"
    #     return eligibility_hold(f"Mandatory_Fail: ADX_Too_Low({adx:.1f}<{adx_floor:.1f};mode={adx_mode})")

    # 波動率過濾：ATR 佔價格比例太高或太低都不開倉。
    # 太高：SL/TP 用 ATR 倍數算出來的停損距離會被放大，同樣倉位金額下
    # 觸發止損時虧的錢遠大於移動止利能鎖住的獲利，是最大幾筆虧損的
    # 共同特徵。太低：市場太安靜時的「突破」更可能是盤整區間的假突破，
    # 沒有真實動能支撐，容易一進場就反轉（實測一批止損反推 ATR 只有
    # 0.07%~0.21%）。兩者一起框出一個波動適中的可交易區間。
    atr_pct = atr / price if price > 0 else 0
    if atr_pct > rules.MAX_ATR_PCT:
        return eligibility_hold(f"Mandatory_Fail: ATR_Too_High({atr_pct:.2%})")
    if atr_pct < atr_min_pct:
        return eligibility_hold(f"Mandatory_Fail: ATR_Too_Low({atr_pct:.2%}<{atr_min_pct:.2%})")

    # 極端 RSI 代表行情已經過熱／過冷，不是更高品質的追價訊號。
    if st_dir == 1 and rsi > rsi_long_max:
        return eligibility_hold(f"Mandatory_Fail: RSI_Overbought({rsi:.1f}>{rsi_long_max:.1f})")
    if st_dir == -1 and rsi < rsi_short_min:
        return eligibility_hold(f"Mandatory_Fail: RSI_Oversold({rsi:.1f}<{rsi_short_min:.1f})")


    # --- 2. 動態評分系統 (Scoring System) ---
    score = 0
    score_details = []

    # A. Keltner 突破分數 (30分) + 收盤確認防假突破
    kc_breakout_buffer = kc_width * rules.KELTNER_BREAKOUT_MARGIN_PCT
    # 防線 1：KC 突破收盤確認 — 取倒數第 2、3 根「已收盤」K 棒（iloc[-3:-1]）
    # 檢查收盤價是否仍在 KC 通道外，最新那根 iloc[-1] 可能尚未收盤不計入。
    # 這樣可過濾「影線剛碰到通道邊界但K棒尚未收出確認」的假突破訊號。
    past_slice = df.iloc[-3:-1]  # 前兩根已收盤K棒
    if len(past_slice) >= 1:
        if st_dir == 1:
            closed_confirmed = int((past_slice['close'] >= (past_slice['kc_upper'] + kc_breakout_buffer)).sum()) >= rules.BREAKOUT_CONFIRM_BARS
        else:
            closed_confirmed = int((past_slice['close'] <= (past_slice['kc_lower'] - kc_breakout_buffer)).sum()) >= rules.BREAKOUT_CONFIRM_BARS
    else:
        closed_confirmed = False  # 資料不足，保守處理視為未確認

    kc_breakout_passed = False
    if st_dir == 1 and price >= (kc_upper + kc_breakout_buffer) and closed_confirmed:
        # LONG 進場前檢查 MA3 趨勢：MA3 必須上升才允許開多單
        ma3_trend = rules.check_ma3_trend(df, lookback=3)
        if ma3_trend != 1:  # MA3 不是上升趨勢
            return eligibility_hold(
                f"LONG_Rejected_By_MA3_Trend: MA3趨勢向下或平盤，不開多單 (ma3_trend={ma3_trend})"
            )
        score += 30
        kc_breakout_passed = True
        score_details.append("KC_Breakout_Pass")
    elif st_dir == -1 and price <= (kc_lower - kc_breakout_buffer) and closed_confirmed:
        score += 30
        kc_breakout_passed = True
        score_details.append("KC_Breakout_Pass")
    elif not closed_confirmed:
        score_details.append(f"KC_Breakout_NoClose({rules.BREAKOUT_CONFIRM_BARS}bar_required)")
    else:
        score_details.append("KC_Breakout_Fail")

    # B. 量能確認分數 (20分)
    if vol_ma_20 > 0 and vol >= (vol_ma_20 * volume_min_ratio):
        score += 20
        score_details.append("Volume_Pass")
    else:
        score_details.append("Volume_Fail")

    # C. RSI 強勢分數 (20分)
    if st_dir == 1 and rsi >= rules.RSI_LONG_THRESHOLD:
        score += 20
        score_details.append("RSI_Pass")
    elif st_dir == -1 and rsi <= rules.RSI_SHORT_THRESHOLD:
        score += 20
        score_details.append("RSI_Pass")
    else:
        score_details.append("RSI_Fail")

    # D. 訊號新鮮度分數：初始突破只占 18/100，避免「方向尚未翻轉」
    # 跟真正 KC 突破同為 30 分，把老趨勢灌成不具預測力的 91+ 高分。
    # freshness_health_score 保留原本 30 分尺度，僅供老化硬門檻使用。
    st_flip_age = rules.bars_since_supertrend_flip(df['st_direction'])
    freshness_ratio = max(0.0, 1.0 - st_flip_age / rules.FRESHNESS_DECAY_BARS) if rules.FRESHNESS_DECAY_BARS > 0 else 0.0
    freshness_health_score = round(freshness_ratio * 30)
    freshness_score = round(freshness_ratio * rules.ENTRY_FRESHNESS_SCORE_MAX)
    score += freshness_score
    score_details.append(f"Freshness({st_flip_age}bars)+{freshness_score}")

    # D2. ADX 動能衰退檢查：SuperTrend 方向沒翻轉不代表動能沒有在退潮——
    # 實測 AAVE/USDT 進場前 8 根 5 分K，ADX 從 19.51 一路降到 14.67 才
    # 進場，方向沒變、新鮮度分數也還高，但這正是「末端趨勢」的典型樣貌：
    # 動能已經在衰退，只是方向還沒真的反轉。ADX 現在比 N 根K棒前低，
    # 且已經低於 WEAK_ENERGY_ADX_THRESHOLD，代表這不是「本來就安靜」
    # 而是「正在退潮」，直接擋單。絕對門檻原本用 ADX_QUALITY_MIN(15)，
    # 但實測 ONDO/USDT 這筆 ADX 從 36.3 衰退到 20 左右進場，衰退幅度
    # 很明顯卻因為還沒低於15分而沒被擋到，改用 WEAK_ENERGY_ADX_THRESHOLD
    # (22)，跟槓桿封頂共用同一套「能量」標準。
    adx_lookback_idx = len(df) - 1 - rules.ADX_DECLINE_LOOKBACK_BARS
    adx_prior = df['adx'].iloc[adx_lookback_idx] if adx_lookback_idx >= 0 else rules.np.nan
    adx_drop = (adx_prior - adx) if not rules.pd.isna(adx_prior) else 0.0
    adx_declining = (
        not rules.pd.isna(adx_prior)
        and adx_drop >= max(rules.ADX_DECLINE_MIN_DROP, adx_prior * rules.ADX_DECLINE_MIN_DROP_RATIO)
    )
    adx_declining_exhausted = False  # --- 使用者要求：解除 ADX 限制，不再因為衰退阻擋開倉 ---

    # D3. 價格乖離檢查：價格距離 EMA30 太遠（用 ATR 正規化衡量），代表
    # 這波已經漲/跌很多才追進場，均值回歸風險高，容易一進場就被拉回。
    # 跟 KC 突破（E4/A）是兩件事——KC 突破只要求價格超出通道邊界一點，
    # 這裡抓的是「超出太多」的極端情況。
    ema30_distance_atr = abs(price - ema_20) / atr if atr > 0 else 0.0
    price_overextended = ema30_distance_atr > rules.EMA_EXTENSION_MAX_ATR_MULT

    # E. 品質細分加分（0~12分）：讓同樣達標 70/80 分的訊號能再分出優劣，
    # 用於同一輪多個候選訊號時挑選最優的下單，而不是隨機/先到先進場。
    # 四項各佔 0~3 分，數值越好加分越多，實測跟虧損大小/勝率相關：
    quality_bonus = 0
    # E1. 波動品質：越接近 [MIN_ATR_PCT, MAX_ATR_PCT] 區間的中點分數越高，
    # 越靠近任一邊界（太安靜或太劇烈）分數越低。不能只獎勵「越低越好」，
    # 因為太低的 ATR 反而是假突破風險（見上面的 Mandatory_Fail 說明）。
    atr_mid = (atr_min_pct + rules.MAX_ATR_PCT) / 2.0
    atr_half_range = (rules.MAX_ATR_PCT - atr_min_pct) / 2.0
    atr_quality = (
        max(0.0, 1.0 - abs(atr_pct - atr_mid) / atr_half_range)
        if atr_half_range > 0 else 0.0
    )
    quality_bonus += round(atr_quality * 3)
    # E2. RSI 品質：獎勵健康動能區中段，不再讓越極端的 RSI 得分越高。
    rsi_ideal = 60.0 if st_dir == 1 else 40.0
    rsi_half_width = max(
        rsi_ideal - rules.RSI_LONG_THRESHOLD if st_dir == 1 else rules.RSI_SHORT_THRESHOLD - rsi_ideal,
        1.0,
    )
    rsi_quality = max(0.0, 1.0 - abs(rsi - rsi_ideal) / rsi_half_width)
    quality_bonus += round(rsi_quality * 3)
    # E3. 量能強度：超過門檻越多代表確認度越高（多 1 倍視為滿分）
    vol_ratio = (vol / vol_ma_20) if vol_ma_20 > 0 else 0.0
    vol_margin = max(0.0, vol_ratio - volume_min_ratio)
    quality_bonus += round(min(vol_margin / 1.0, 1.0) * 3)
    # E4. 趨勢強度（ADX）：ADX 越高代表越像真的有趨勢動能撐著，越低越像
    # 盤整期雜訊——KC 突破配上低 ADX，正是假突破最常見的樣貌之一。
    # ADX_QUALITY_MIN 以下不加分，ADX_QUALITY_FULL 以上視為滿分。
    adx_ratio = (adx - rules.ADX_QUALITY_MIN) / (rules.ADX_QUALITY_FULL - rules.ADX_QUALITY_MIN)
    quality_bonus += round(min(max(adx_ratio, 0.0), 1.0) * 3)
    # ADX 仍高於品質底線時，下降只代表趨勢強度轉弱，不能直接取消；
    # 輕扣 1 分品質，保留高分訊號進入回踩確認。低於底線才由硬性規則攔截。
    adx_decline_soft_penalty = 1 if adx_declining and not adx_declining_exhausted else 0
    if adx_decline_soft_penalty:
        quality_bonus = max(0, quality_bonus - adx_decline_soft_penalty)
        score_details.append(
            f"ADX_Declining_Soft-1({adx:.1f}<{adx_prior:.1f};floor={rules.WEAK_ENERGY_ADX_THRESHOLD:.1f})"
        )

    score += quality_bonus
    if quality_bonus > 0:
        score_details.append(f"Quality+{quality_bonus}")

    raw_score_before_btc = min(100, score)
    score = raw_score_before_btc
    if btc_regime["score_penalty"] > 0:
        score = max(0, score - btc_regime["score_penalty"])
        score_details.append(
            f"BTC_Contrary-{btc_regime['score_penalty']}({raw_score_before_btc}→{score})"
        )
    btc_context = {
        "eligible": True,
        "score_stage": "INITIAL",
        "raw_score": raw_score_before_btc,
        "btc_adjusted_score": score,
        "score_components": {
            "kc": 30 if kc_breakout_passed else 0,
            "volume": 20 if "Volume_Pass" in score_details else 0,
            "rsi": 20 if "RSI_Pass" in score_details else 0,
            "freshness": freshness_score,
            "quality": quality_bonus,
        },
        "btc_regime_mode": btc_regime["mode"],
        "btc_direction_1h": int(btc_st_direction_1h or 0),
        "btc_score_penalty": btc_regime["score_penalty"],
        "btc_allocation_factor": btc_regime["allocation_factor"],
        "btc_pre_penalty_score": raw_score_before_btc,
        "diagnostics": signal_diagnostics(),
    }

    def scored_hold(reason: str) -> dict:
        return {"action": "HOLD", "score": score, "reason": reason, **btc_context}

    # --- 3. 回調狙擊最終決策 (Pullback Sniper Mode) ---
    # 修正核心：KC 突破是「訊號觸發」，等價格回踩 KC 軌道後才是「進場時機」
    # 進場門檻：總分 >= MIN_SCORE_THRESHOLD（預設 75 分）
    # 額外防線：新鮮度子分數太低（趨勢已經很舊）直接擋單，不管總分靠
    # 其他項目湊得多高——避免「已經開始老化、快要反轉的趨勢尾端，
    # 靠其他項目湊夠分數壓線擠進場」這種樣貌。
    if score >= rules.MIN_SCORE_THRESHOLD and not kc_breakout_passed:
        return scored_hold(
            f"Mandatory_Fail: KC_Breakout_Unconfirmed | Score({score}) | {', '.join(score_details)}"
        )

    if score >= rules.MIN_SCORE_THRESHOLD and quality_bonus < rules.ENTRY_MIN_QUALITY_BONUS:
        return scored_hold(
            f"Mandatory_Fail: Entry_Quality_Too_Low"
            f"({quality_bonus}<{rules.ENTRY_MIN_QUALITY_BONUS}) | Score({score}) | "
            f"{', '.join(score_details)}"
        )

    if score >= rules.MIN_SCORE_THRESHOLD and freshness_health_score < rules.MIN_FRESHNESS_SCORE:
        return scored_hold(
            f"Mandatory_Fail: Freshness_Too_Stale({st_flip_age}bars) | Score({score}) | {', '.join(score_details)}"
        )

    # 額外防線：只有 ADX 已跌破品質底線且持續衰退才硬擋。ADX 仍在
    # 品質底線以上時已於品質分輕扣 1 分，不取消仍具強度的趨勢訊號。
    if score >= rules.MIN_SCORE_THRESHOLD and adx_declining_exhausted:
        return scored_hold(
            f"Mandatory_Fail: ADX_Declining_Exhaustion({adx:.1f}<{adx_prior:.1f}) | Score({score}) | {', '.join(score_details)}"
        )

    # 額外防線：價格已經乖離 EMA30 太遠（見上面 D3），代表這波已經漲/
    # 跌很多才追進場，均值回歸風險高，不管總分靠其他項目湊得多高。
    if score >= rules.MIN_SCORE_THRESHOLD and price_overextended:
        return scored_hold(
            f"Mandatory_Fail: Price_Overextended({ema30_distance_atr:.1f}x_ATR) | Score({score}) | {', '.join(score_details)}"
        )

    # 額外防線：大週期（1h）本身的動能也在衰退（見 engine.py
    # update_1h_trend_cache 用同一批1h K線算的 ADX 衰退判斷），代表
    # 不只是5分K的小趨勢要提防，連大方向本身都已經在做頭/做底，
    # 這是5分K的新鮮度/ADX檢查看不到的更高層級末端訊號。
    if rules.MIN_SCORE_THRESHOLD <= score < 90 and trend_1h_declining:
        return scored_hold(
            f"Mandatory_Fail: 1h_Trend_Declining | Score({score}) | {', '.join(score_details)}"
        )
    if score >= 90 and trend_1h_declining:
        # 90+ 現價 Maker 試行：高完整度突破不再被 1h ADX 衰退單獨否決。
        # 方向、ATR、RSI、KC、品質等前置資格仍已全部通過。
        score_details.append("1h_Trend_Declining_90Plus_Allowed")

    if score >= rules.MIN_SCORE_THRESHOLD:
        pullback_depth = rules.get_pullback_target_depth(score)
        kc_edge = kc_upper if st_dir == 1 else kc_lower
        side = "LONG" if st_dir == 1 else "SHORT"
        current_maker = score >= 90
        if current_maker:
            pullback_target = float(price)
            pullback_distance = 0.0
        else:
            pullback_target, pullback_distance, pullback_room_ok = rules.compute_pullback_target(
                kc_edge, ema_20, atr, side, score
            )
            if not pullback_room_ok:
                return scored_hold(
                    f"Mandatory_Fail: Pullback_Range_Too_Narrow"
                    f"({pullback_distance / max(atr, 1e-12):.2f}ATR<"
                    f"{rules.PULLBACK_TARGET_MIN_ATR_MULT:.2f}ATR) | Score({score}) | "
                    f"{', '.join(score_details)}"
                )
        confirmation_reason = (
            f"ADX {adx:.1f}←{adx_prior:.1f}仍高於{rules.WEAK_ENERGY_ADX_THRESHOLD:g}，品質-1，等待回調二次確認"
            if adx_decline_soft_penalty
            else "90+分現價Maker掛單" if current_maker
            else "等待回調至KC區後二次確認"
        )
        entry_mode = "CURRENT_MAKER" if current_maker else "PULLBACK"
        downgrade_note = " | CurrentPrice_PostOnly" if current_maker else " | MarketChase_Disabled"
        if st_dir == 1:
            dist = (price - kc_upper) / kc_upper
            return {
                "action": "WAIT_PULLBACK", "side": "LONG",
                "price": price, "atr": atr,
                "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                "target_zone": pullback_target, "ema_20": ema_20,
                "pullback_depth": pullback_depth,
                "pullback_distance_atr": pullback_distance / max(atr, 1e-12),
                "entry_mode": entry_mode,
                "confirmation_reason": confirmation_reason,
                **btc_context,
                "reason": f"Pullback_WAIT({score}) | dist={dist:.2%} | Target={pullback_target:.4f} | {', '.join(score_details)}{downgrade_note}"
            }
        else:  # SHORT
            # 改進：空單確認後直接進場，不必等紅K回到中軌
            # 若分數足夠高（>=80），允許直接市價進場；否則仍等待回調
            dist = (kc_lower - price) / kc_lower
            if score >= 80:  # SHORT 直接進場條件：分數足夠高
                return {
                    "action": "ENTER_MARKET", "side": "SHORT",
                    "entry_mode": "SHORT_FAST_ENTRY",
                    "price": price, "atr": atr,
                    "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                    "ema_20": ema_20,
                    "reason": f"SHORT_FastEntry({score}) | dist={dist:.2%} | 確認空單無需等待回調 | {', '.join(score_details)}{downgrade_note}",
                    **btc_context,
                }
            else:
                # 分數低於 80 時，仍需等待回調
                return {
                    "action": "WAIT_PULLBACK", "side": "SHORT",
                    "price": price, "atr": atr,
                    "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                    "target_zone": pullback_target, "ema_20": ema_20,
                    "pullback_depth": pullback_depth,
                    "pullback_distance_atr": pullback_distance / max(atr, 1e-12),
                    "entry_mode": entry_mode,
                    "confirmation_reason": confirmation_reason,
                    **btc_context,
                    "reason": f"Pullback_WAIT({score}) | dist={dist:.2%} | Target={pullback_target:.4f} | {', '.join(score_details)}{downgrade_note}"
                }

    return scored_hold(f"Score_Low({score}) | {', '.join(score_details)}")
