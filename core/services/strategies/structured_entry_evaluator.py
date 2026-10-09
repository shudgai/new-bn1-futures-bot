"""Evaluate the structured breakout, support, and momentum entry candidates."""

from typing import Any

import pandas as pd


def evaluate_structured_entry(
    strategy: Any,
    rules: Any,
    df: pd.DataFrame,
    ema_50_1h: float | None = None,
    st_direction_1h: int | None = None,
    btc_st_direction_1h: int = 0,
    symbol: str | None = None,
    indicators_precomputed: bool = False,
    is_dca_check: bool = False,
) -> dict:
    """Three closed-bar entries without MA5: breakout, support pullback, momentum cross."""
    if len(df) < max(65, rules.STRUCTURED_SWING_LOOKBACK + 2):
        return {"action": "HOLD", "reason": "5m K線資料不足"}
    if not indicators_precomputed:
        df = strategy.compute_indicators(df)
    curr, prev = df.iloc[-1], df.iloc[-2]

    from core.indicators import analyze_candle_pattern
    candle_pattern = analyze_candle_pattern(curr)

    direction = int(curr["st_direction"])
    side = "LONG" if direction == 1 else "SHORT"


    price = float(curr["close"])
    volume_ratio = float(curr["volume"] / curr["vol_ma_20"]) if float(curr["vol_ma_20"]) > 0 else 0.0
    quality_gate = rules.evaluate_entry_quality_gate(
        side=side,
        price=price,
        atr=float(curr["atr"]),
        volume_ratio=volume_ratio,
        score=85,
        df=df,
        min_volume_ratio=rules.KELTNER_MIN_VOLUME_RATIO,
    )
    if quality_gate["blocked"]:
        return {
            "action": "HOLD",
            "side": side,
            "score": 0,
            "reason": quality_gate["reason"],
            "btc_regime_mode": "ALIGNED",
            "btc_allocation_factor": 1.0,
            "volume_ratio": volume_ratio,
            "price": price,
        }
    atr = float(curr["atr"]) if not rules.pd.isna(curr["atr"]) else price * 0.015
    volume = float(curr["volume"])
    volume_ma = float(curr["vol_ma_20"]) if not rules.pd.isna(curr["vol_ma_20"]) else 0.0
    volume_ratio = volume / volume_ma if volume_ma > 0 else 0.0
    aligned = st_direction_1h in (None, direction)
    btc_contrary = bool(
        rules.BTC_REGIME_FILTER_ENABLED
        and btc_st_direction_1h in (-1, 1)
        and btc_st_direction_1h != direction
    )
    # 結構單不因 BTC 反向完全消失，但必須同步降分與半倉。
    btc_score_penalty = rules.BTC_REGIME_SCORE_PENALTY if btc_contrary else 0
    btc_allocation_factor = 0.5 if btc_contrary else 1.0
    common = {
        "side": side, "price": price, "atr": atr,
        "signal_candle_low": float(curr["low"]),
        "signal_candle_high": float(curr["high"]),
        "volume_ratio": volume_ratio,
        "btc_regime_mode": "CONTRARY" if btc_contrary else "ALIGNED",
        "btc_score_penalty": btc_score_penalty,
        "btc_allocation_factor": btc_allocation_factor,
    }

    if side == "LONG":
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
            # 拒絕在接近前高點時遇到 MACD 頂背離還繼續做多 (避免接頂)
            return {
                "action": "HOLD",
                "reason": f"Bearish_MACD_Divergence_Near_Recent_High(距離前高{recent_high_distance_pct:.2%}, MACD 背離顯示空頭仍強，拒絕做多)",
                "side": side,
                "score": 0,
                "btc_regime_mode": "ALIGNED",
                "btc_allocation_factor": 1.0,
                "volume_ratio": volume_ratio,
                "price": price,
            }
    elif side == "SHORT":
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
            curr = curr.copy()
            curr['eligibility_note'] = (
                f"Near_Recent_High_No_Reversal_Confirmation(距離前高{recent_high_distance_pct:.2%}, 近期高點附近須先跌破前一根收盤與確認反轉)"
            )
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
            # 拒絕在接近前低點時遇到 MACD 底背離還繼續做空 (避免接底)
            return {
                "action": "HOLD",
                "reason": f"Bullish_MACD_Divergence_Near_Recent_Low(距離近期低點{recent_low_distance_pct:.2%}, MACD 背離顯示多頭仍可反彈，拒絕空單)",
                "side": side,
                "score": 0,
                "btc_regime_mode": "ALIGNED",
                "btc_allocation_factor": 1.0,
                "volume_ratio": volume_ratio,
                "price": price,
            }

    # MomentumCross 只把交叉視為候選訊號。正式進場延後一根已收盤 K 棒，
    # 要求收盤價沿訊號方向續走且交叉仍有效，避免沒有延續便立即追價。
    pre_cross = df.iloc[-3]
    macd_hist_cross = (
        float(pre_cross["macd_hist"]) <= 0 < float(prev["macd_hist"])
        if side == "LONG" else float(pre_cross["macd_hist"]) >= 0 > float(prev["macd_hist"])
    )
    macd_line_cross = (
        float(pre_cross["macd_line"]) <= float(pre_cross["macd_signal"])
        and float(prev["macd_line"]) > float(prev["macd_signal"])
        and float(prev["macd_line"]) >= 0
        if side == "LONG" else
        float(pre_cross["macd_line"]) >= float(pre_cross["macd_signal"])
        and float(prev["macd_line"]) < float(prev["macd_signal"])
        and float(prev["macd_line"]) <= 0
    )
    rsi_cross = (
        float(pre_cross["rsi"]) < 50 and float(prev["rsi"]) >= rules.STRUCTURED_RSI_LONG_TRIGGER
        if side == "LONG" else
        float(pre_cross["rsi"]) > 50 and float(prev["rsi"]) <= rules.STRUCTURED_RSI_SHORT_TRIGGER
    )
    if rules.ENABLE_MOMENTUM_CROSS_ENTRY and aligned and (macd_hist_cross or macd_line_cross or rsi_cross):
        trigger_still_valid = (
            (macd_hist_cross and (
                float(curr["macd_hist"]) > 0 if side == "LONG"
                else float(curr["macd_hist"]) < 0
            ))
            or (macd_line_cross and (
                float(curr["macd_line"]) > float(curr["macd_signal"])
                if side == "LONG" else
                float(curr["macd_line"]) < float(curr["macd_signal"])
            ))
            or (rsi_cross and (
                float(curr["rsi"]) >= rules.STRUCTURED_RSI_LONG_TRIGGER
                if side == "LONG" else
                float(curr["rsi"]) <= rules.STRUCTURED_RSI_SHORT_TRIGGER
            ))
        )
        continuation_confirmed = (
            price > float(prev["close"])
            and price > float(curr["open"])
            and trigger_still_valid
            if side == "LONG" else
            price < float(prev["close"])
            and price < float(curr["open"])
            and trigger_still_valid
        )
        if rules.MOMENTUM_CROSS_REQUIRE_CONTINUATION and not continuation_confirmed:
            signal_close = float(prev["close"])
            direction_word = "高於" if side == "LONG" else "低於"
            return {
                "action": "HOLD", "side": side, "score": 0,
                "momentum_continuation_confirmed": False,
                "reason": (
                    f"MomentumCross_{side} 等待價格延續：收盤 {price:.6g} 尚未"
                    f"{direction_word}訊號棒收盤 {signal_close:.6g}，或交叉已失效"
                ),
                **common,
            }

        momentum_swing = df.iloc[-(rules.STRUCTURED_SWING_LOOKBACK + 1):-1]
        momentum_prior_high = float(momentum_swing["high"].max())
        momentum_prior_low = float(momentum_swing["low"].min())
        profit_room_pct = (
            max(0.0, (momentum_prior_high - price) / price)
            if side == "LONG" else
            max(0.0, (price - momentum_prior_low) / price)
        )
        if profit_room_pct < rules.MOMENTUM_CROSS_MIN_PROFIT_ROOM_PCT:
            return {
                "action": "HOLD", "side": side, "score": 0,
                "momentum_continuation_confirmed": continuation_confirmed,
                "profit_room_pct": profit_room_pct,
                "reason": (
                    f"MomentumCross_{side} 預估獲利空間不足：目前"
                    f"{profit_room_pct:.2%}<最低"
                    f"{rules.MOMENTUM_CROSS_MIN_PROFIT_ROOM_PCT:.2%}，拒絕進場"
                ),
                **common,
            }

        triggers = []
        if macd_hist_cross or macd_line_cross:
            triggers.append("MACD交叉")
        if rsi_cross:
            triggers.append("RSI穿越50")
        trigger_text = "+".join(triggers)

        # --- K 線形態防護：過濾假突破 ---
        if side == "LONG" and candle_pattern.get("is_shooting_star"):
            return rules.eligibility_hold(
                f"MomentumCross_{side} 拒絕：出現流星線 (Shooting Star) 假突破"
            )
        if side == "SHORT" and candle_pattern.get("is_hammer"):
            return rules.eligibility_hold(
                f"MomentumCross_{side} 拒絕：出現錘頭線 (Hammer) 假突破"
            )

        return {
            "action": "ENTER_MARKET", "entry_mode": "MOMENTUM_CROSS",
            "score": 80 - btc_score_penalty,
            "momentum_continuation_confirmed": continuation_confirmed,
            "profit_room_pct": profit_room_pct,
            # MomentumCross follows an aligned 5m/1h trend. Treating it as
            # a BOUNCE position makes the short-window bounce guard close it
            # before the R-based trailing exit has a chance to run.
            "profit_profile": "TREND_EXTENSION",
            "reason": (
                f"MomentumCross_{side}｜{trigger_text}｜價格延續確認｜"
                f"預估空間{profit_room_pct:.2%}"
            ), **common,
        }


    # 1h EMA50 大週期趨勢過濾：開倉方向必須與 1h EMA50 大趨勢同向
    if rules.ENABLE_1H_EMA50_FILTER and not is_dca_check and ema_50_1h is not None and ema_50_1h > 0:
        ema_lower_bound = ema_50_1h * (1.0 - rules.STRUCTURED_1H_EMA50_TOLERANCE_PCT)
        ema_upper_bound = ema_50_1h * (1.0 + rules.STRUCTURED_1H_EMA50_TOLERANCE_PCT)
        if side == "LONG" and price < ema_lower_bound:
            return {
                "action": "HOLD", "side": side, "score": 0,
                "reason": f"價格低於 1h EMA50 容許區，逆勢做多拒絕開倉（價格 {price:.6g} < 下界 {ema_lower_bound:.6g}，容許{rules.STRUCTURED_1H_EMA50_TOLERANCE_PCT:.2%}）", **common
            }
        elif side == "SHORT" and price > ema_upper_bound:
            return {
                "action": "HOLD", "side": side, "score": 0,
                "reason": f"價格高於 1h EMA50 容許區，逆勢做空拒絕開倉（價格 {price:.6g} > 上界 {ema_upper_bound:.6g}，容許{rules.STRUCTURED_1H_EMA50_TOLERANCE_PCT:.2%}）", **common
            }

    # 計算支撐與壓力區（基於最近 24 根已收盤 of 5m K棒）
    # PRICE_NEAR_SUPPORT_PCT=0 則停用此條件（與 evaluate_signal 保持一致）
    _sp_near_pct = float(getattr(rules._core_config, 'PRICE_NEAR_SUPPORT_PCT', rules.PRICE_NEAR_SUPPORT_PCT))
    if not is_dca_check and _sp_near_pct > 0 and symbol is not None and 'low' in df.columns and 'high' in df.columns and len(df) >= 25:
        past_24_bars = df.iloc[-25:-1]
        support_level = float(past_24_bars['low'].min())
        resistance_level = float(past_24_bars['high'].max())

        # 做多：必須在支撐位 N% 內
        if side == "LONG" and price > support_level * (1.0 + _sp_near_pct):
            return {
                "action": "HOLD", "side": side, "score": 0,
                "reason": f"價格不在支撐區{_sp_near_pct:.0%}內（當前 {price:.6g} > 支撐 {support_level:.6g}*{1.0+_sp_near_pct:.3f}）", **common
            }

        # 做空：必須在壓力位 N% 內
        if side == "SHORT" and price < resistance_level * (1.0 - _sp_near_pct):
            return {
                "action": "HOLD", "side": side, "score": 0,
                "reason": f"價格不在壓力區{_sp_near_pct:.0%}內（當前 {price:.6g} < 壓力 {resistance_level:.6g}*{1.0-_sp_near_pct:.3f}）", **common
            }


    swing = df.iloc[-(rules.STRUCTURED_SWING_LOOKBACK + 1):-1]
    prior_high = float(swing["high"].max())
    prior_low = float(swing["low"].min())
    kc_break = (
        price > float(curr["kc_upper"]) if side == "LONG"
        else price < float(curr["kc_lower"])
    )
    structure_break = price > prior_high if side == "LONG" else price < prior_low
    if rules.ENABLE_BREAKOUT_ENTRY and aligned and (kc_break or structure_break) and volume_ratio >= rules.STRUCTURED_VOLUME_MIN_RATIO:
        trigger = "KC上軌" if side == "LONG" and kc_break else "KC下軌" if side == "SHORT" and kc_break else "前高" if side == "LONG" else "前低"

        # 爆量只代表突破候選，不代表可直接追價。實績顯示「量能 >= 2x
        # 就給 95 分並市價進場」會在短線耗竭點取得最大倉位；所有突破
        # 統一等待 EMA30 附近回踩，以 Maker 限價單成交。
        ema30 = float(curr["ema_20"])
        from core.config import BREAKOUT_PULLBACK_ATR_MULT
        if side == "LONG":
            pullback_target = ema30 + BREAKOUT_PULLBACK_ATR_MULT * atr
            pullback_target = min(pullback_target, price * 0.9995)
        else:
            pullback_target = ema30 - BREAKOUT_PULLBACK_ATR_MULT * atr
            pullback_target = max(pullback_target, price * 1.0005)
        return {
            "action": "ENTER_LIMIT", "entry_mode": "BREAKOUT",
            "score": 100,
            "target_price": pullback_target,
            "signal_candle_low": float(curr["low"]),
            "signal_candle_high": float(curr["high"]),
            "reason": f"Breakout_{side}｜突破{trigger}｜量能{volume_ratio:.2f}x｜等待EMA30回踩Maker",
            "prior_high": prior_high, "prior_low": prior_low, **common,
        }

    ema30 = float(curr["ema_20"])
    ema60_series = df["close"].ewm(span=60, adjust=False).mean()
    ema60 = float(ema60_series.iloc[-1])
    supports = [("5m EMA30", ema30), ("5m EMA60", ema60)]
    if ema_50_1h is not None:
        supports.append(("1h EMA50", float(ema_50_1h)))
    support_name, support_price = min(supports, key=lambda item: abs(price - item[1]))
    support_distance_atr = abs(price - support_price) / max(atr, 1e-12)
    location_memory = None
    for age in range(max(1, rules.SUPPORT_PULLBACK_LOCATION_MEMORY_BARS)):
        row_pos = len(df) - 1 - age
        if row_pos < 0:
            break
        row = df.iloc[row_pos]
        if int(row["st_direction"]) != direction:
            continue
        row_price = float(row["close"])
        row_atr = float(row["atr"]) if not rules.pd.isna(row["atr"]) else atr
        row_supports = [
            ("5m EMA30", float(row["ema_20"])),
            ("5m EMA60", float(ema60_series.iloc[row_pos])),
        ]
        if ema_50_1h is not None:
            row_supports.append(("1h EMA50", float(ema_50_1h)))
        row_name, row_support = min(
            row_supports, key=lambda item: abs(row_price - item[1])
        )
        row_distance = abs(row_price - row_support) / max(row_atr, 1e-12)
        if row_distance <= rules.STRUCTURED_SUPPORT_NEAR_ATR:
            location_memory = {
                "age": age, "name": row_name, "price": row_support,
                "close": row_price, "distance": row_distance,
            }
            break
    near_support = location_memory is not None
    candle_open = float(curr["open"])
    candle_body_atr = abs(price - candle_open) / max(atr, 1e-12)
    macd_hist = float(curr["macd_hist"])
    prev_macd_hist = float(prev["macd_hist"])
    confirmation_memory = None
    for age in range(max(1, rules.SUPPORT_PULLBACK_CONFIRM_MEMORY_BARS)):
        row_pos = len(df) - 1 - age
        prev_pos = row_pos - 1
        if prev_pos < 0:
            break
        row = df.iloc[row_pos]
        prior_row = df.iloc[prev_pos]
        if int(row["st_direction"]) != direction:
            continue
        row_price = float(row["close"])
        row_open = float(row["open"])
        row_high = float(row["high"])
        row_low = float(row["low"])
        row_atr = float(row["atr"]) if not rules.pd.isna(row["atr"]) else atr
        row_range = max(row_high - row_low, 1e-12)
        row_close_location = (row_price - row_low) / row_range
        row_reversal = (
            row_price > row_open and row_close_location >= 0.60
            if side == "LONG"
            else row_price < row_open and row_close_location <= 0.40
        )
        row_hist = float(row["macd_hist"])
        prior_hist = float(prior_row["macd_hist"])
        row_macd_improving = (
            row_hist > prior_hist if side == "LONG" else row_hist < prior_hist
        )
        row_body_atr = abs(row_price - row_open) / max(row_atr, 1e-12)
        if (
            (row_reversal or row_macd_improving)
            and row_body_atr >= rules.SUPPORT_PULLBACK_MIN_BODY_ATR_MULT
        ):
            confirmation_memory = {
                "age": age, "reversal": row_reversal,
                "body_atr": row_body_atr,
            }
            break
    confirmation_recent = confirmation_memory is not None
    confirmed_body_atr = (
        float(confirmation_memory["body_atr"])
        if confirmation_memory else candle_body_atr
    )
    volume_healthy = (
        volume_ma > 0
        and rules.SUPPORT_PULLBACK_MIN_VOLUME_RATIO <= volume_ratio
        <= rules.SUPPORT_PULLBACK_MAX_VOLUME_RATIO
    )
    rsi = float(curr["rsi"])
    previous_rsi = float(prev["rsi"])
    rsi_extreme = (
        rsi > rules.SUPPORT_PULLBACK_RSI_LONG_MAX
        if side == "LONG" else rsi < rules.SUPPORT_PULLBACK_RSI_SHORT_MIN
    )
    if rsi_extreme:
        boundary = (
            rules.SUPPORT_PULLBACK_RSI_LONG_MAX
            if side == "LONG" else rules.SUPPORT_PULLBACK_RSI_SHORT_MIN
        )
        label = "過熱" if side == "LONG" else "過冷"
        return {
            "action": "HOLD", "side": side, "score": 0,
            "reason": f"RSI{label}（目前{rsi:.1f}，界限{boundary:g}），拒絕追價進場",
            **common,
        }
    rsi_ok = (
        rules.SUPPORT_PULLBACK_RSI_LONG_MIN <= rsi <= rules.SUPPORT_PULLBACK_RSI_LONG_MAX and rsi > previous_rsi
        if side == "LONG"
        else rules.SUPPORT_PULLBACK_RSI_SHORT_MIN <= rsi <= rules.SUPPORT_PULLBACK_RSI_SHORT_MAX and rsi < previous_rsi
    )

    # 【改進方案】強化多頭過濾：LONG 交易勝率 (53.42%) 與止損率 (24.66%) 均顯著遜於 SHORT
    if side == "LONG":
        # 提高多頭的量能要求（防止虛假突破）
        min_volume_ratio_long = max(rules.SUPPORT_PULLBACK_MIN_VOLUME_RATIO, rules.SUPPORT_PULLBACK_MIN_VOLUME_RATIO_LONG)
        volume_healthy_long = volume_ma > 0 and volume_ratio >= min_volume_ratio_long
        if not volume_healthy_long:
            return {
                "action": "HOLD", "side": side, "score": 0,
                "reason": f"多頭交易量能不足：{volume_ratio:.2f}x < {min_volume_ratio_long:.2f}x，避免虛假突破",
                **common,
            }

        # 提高多頭的 RSI 進場門檻（防止追高）
        rsi_long_min_enhanced = max(rules.SUPPORT_PULLBACK_RSI_LONG_MIN, rules.SUPPORT_PULLBACK_RSI_LONG_MIN_ENHANCED)
        if rsi < rsi_long_min_enhanced:
            return {
                "action": "HOLD", "side": side, "score": 0,
                "reason": f"多頭交易 RSI 門檻提高：{rsi:.1f} < {rsi_long_min_enhanced:.1f}，等待更強勢信號",
                **common,
            }

        # 檢查 Keltner Channel 寬度（避免在通道極窄時進場）
        kc_width = float(curr["kc_upper"]) - float(curr["kc_lower"])
        kc_width_atr_mult = kc_width / max(atr, 1e-12)
        min_kc_width_long = rules.KELTNER_MIN_WIDTH_ATR_MULT_LONG * atr
        if kc_width < min_kc_width_long:
            return {
                "action": "HOLD", "side": side, "score": 0,
                "reason": f"多頭 Keltner 通道過窄保護：{kc_width_atr_mult:.2f}x ATR < {rules.KELTNER_MIN_WIDTH_ATR_MULT_LONG:.2f}x，通道擴張才進場",
                **common,
            }

    adx = float(curr["adx"]) if not rules.pd.isna(curr["adx"]) else 0.0
    atr_pct = atr / price if price > 0 else 0.0
    quality_ok = rules.ADX_QUALITY_MIN <= adx and rules.MIN_ATR_PCT <= atr_pct <= rules.MAX_ATR_PCT

    # 等待中的訊號提供「準備度」，但不冒充正式進場分數。位置與確認事件
    # 可在短期記憶視窗內組合；方向、量能、RSI與品質仍須以最新K棒通過。
    trend_points = 20 if aligned else 0
    effective_support_distance = (
        float(location_memory["distance"])
        if location_memory else support_distance_atr
    )
    location_progress = max(
        0.0,
        1.0 - max(0.0, effective_support_distance - rules.STRUCTURED_SUPPORT_NEAR_ATR),
    )
    location_points = round(20 * location_progress)
    reversal_points = 20 if confirmation_recent else 0
    body_points = round(
        10 * min(
            confirmed_body_atr / max(rules.SUPPORT_PULLBACK_MIN_BODY_ATR_MULT, 1e-12), 1.0
        )
    )
    if volume_ma <= 0:
        volume_progress = 0.0
    elif volume_ratio < rules.SUPPORT_PULLBACK_MIN_VOLUME_RATIO:
        volume_progress = volume_ratio / max(rules.SUPPORT_PULLBACK_MIN_VOLUME_RATIO, 1e-12)
    else:
        volume_progress = max(
            0.0,
            1.0 - max(0.0, volume_ratio - rules.SUPPORT_PULLBACK_MAX_VOLUME_RATIO)
            / max(rules.SUPPORT_PULLBACK_MAX_VOLUME_RATIO, 1e-12),
        )
    volume_points = round(10 * volume_progress)
    if not volume_healthy:
        volume_points = min(volume_points, 9)
    if side == "LONG":
        rsi_progress = min(max((rsi - (rules.SUPPORT_PULLBACK_RSI_LONG_MIN - 10.0)) / 10.0, 0.0), 1.0)
        rsi_trending = rsi > previous_rsi
    else:
        rsi_progress = min(max(((rules.SUPPORT_PULLBACK_RSI_SHORT_MAX + 10.0) - rsi) / 10.0, 0.0), 1.0)
        rsi_trending = rsi < previous_rsi
    rsi_points = min(10, round(7 * rsi_progress) + (3 if rsi_trending else 0))
    quality_points = (
        (5 if adx >= rules.ADX_QUALITY_MIN else round(5 * max(adx, 0.0) / max(rules.ADX_QUALITY_MIN, 1e-12)))
        + (5 if rules.MIN_ATR_PCT <= atr_pct <= rules.MAX_ATR_PCT else 0)
    )
    # 進度分可以接近滿分，但硬條件未通過時該項不得因 round() 進位
    # 成滿分，否則會出現「100/100 但尚缺量能／RSI」的矛盾。
    if not near_support:
        location_points = min(location_points, 19)
    if not rsi_ok:
        rsi_points = min(rsi_points, 9)
    if not quality_ok:
        quality_points = min(quality_points, 9)
    readiness_components = {
        "trend": trend_points, "location": location_points,
        "reversal": reversal_points, "body": body_points,
        "volume": volume_points, "rsi": rsi_points,
        "quality": quality_points,
    }
    readiness_score = int(sum(readiness_components.values()))
    prerequisites_ready = (
        aligned and near_support and confirmation_recent
        and volume_healthy and rsi_ok and quality_ok
    )
    if not prerequisites_ready:
        readiness_score = min(readiness_score, 99)

    if prerequisites_ready:
        # 【改進方案】高分值陷阱保護：95+ 分信號在極端波動時表現極差
        if readiness_score >= rules.HIGH_SCORE_THRESHOLD:
            atr_pct = atr / price if price > 0 else 0.0
            if atr_pct > rules.HIGH_SCORE_ATR_LIMIT_PCT:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "readiness_score": readiness_score,
                    "readiness_components": readiness_components,
                    "reason": (
                        f"高分值信號波動過大保護：準備度 {readiness_score}/100 但 ATR% "
                        f"{atr_pct:.4f} > 限制 {rules.HIGH_SCORE_ATR_LIMIT_PCT:.4f}，"
                        f"避免在極端行情進場"
                    ),
                    **common
                }

        if rules.BOTTOM_FILTER_ENABLED:
            rsi_15m = float(curr["rsi_15m"]) if "rsi_15m" in curr and not rules.pd.isna(curr["rsi_15m"]) else rsi
            macd_div = False
            if "macd_hist" in df.columns and "close" in df.columns:
                macd_div = rules.detect_macd_divergence(df, side)
            is_bottom_ok = False
            if side == "LONG":
                is_bottom_ok = (rsi_15m <= rules.BOTTOM_OVERSOLD_RSI_15M_LIMIT) or macd_div
            else:
                is_bottom_ok = (rsi_15m >= rules.BOTTOM_OVERBOUGHT_RSI_15M_LIMIT) or macd_div
            if not is_bottom_ok:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "readiness_score": readiness_score,
                    "readiness_components": readiness_components,
                    "reason": f"未滿足底部抄底條件：15m RSI({rsi_15m:.1f}) 未達限制，且無 MACD 背離偵測",
                    **common
                }

        reversal_desc = (
            "收綠K反彈" if side == "LONG" else "收紅K反轉"
        ) if confirmation_memory["reversal"] else "MACD動能改善"
        memory_note = (
            f"位置{location_memory['age']}根內、確認{confirmation_memory['age']}根內"
            if location_memory["age"] or confirmation_memory["age"] else "即時確認"
        )
        entry_support_name = str(location_memory["name"])
        anchor_price = float(location_memory["close"])
        target_price = (
            min(price, anchor_price) - atr * rules.SUPPORT_PULLBACK_MAKER_OFFSET_ATR_MULT
            if side == "LONG"
            else max(price, anchor_price) + atr * rules.SUPPORT_PULLBACK_MAKER_OFFSET_ATR_MULT
        )
        body_score = round(min(confirmed_body_atr / 0.50, 1.0) * 4)
        support_score = round(
            max(0.0, 1.0 - effective_support_distance / max(rules.STRUCTURED_SUPPORT_NEAR_ATR, 1e-12)) * 4
        )
        rsi_strength = (
            max(0.0, rsi - rules.SUPPORT_PULLBACK_RSI_LONG_MIN)
            if side == "LONG"
            else max(0.0, rules.SUPPORT_PULLBACK_RSI_SHORT_MAX - rsi)
        )
        rsi_score = round(min(rsi_strength / 8.0, 1.0) * 4)
        adx_score = round(min(max(adx - rules.ADX_QUALITY_MIN, 0.0) / 18.0, 1.0) * 4)

        # --- K 線反轉形態加分 ---
        pattern_score = 0
        if side == "LONG" and candle_pattern.get("is_hammer"):
            pattern_score = 5
        elif side == "SHORT" and candle_pattern.get("is_shooting_star"):
            pattern_score = 5

        score = max(
            0,
            min(91, 75 + body_score + support_score + rsi_score + adx_score + pattern_score)
            - btc_score_penalty,
        )
        profit_room_pct = (
            max(0.0, (prior_high - target_price) / target_price)
            if side == "LONG"
            else max(0.0, (target_price - prior_low) / target_price)
        )
        # 所有進場一律要求完整獲利空間；不再讓 100/100 訊號以半倉
        # 繞過門檻，避免高準備度但只剩交易成本等級空間的低價值交易。
        # 低空間小倉例外已關閉：若獲利空間不足 1%，直接拒絕不開倉。
        candidate_capture_ratio = rules.get_bounce_capture_ratio(score)
        high_readiness_low_room = False
        if profit_room_pct < rules.MIN_ENTRY_PROFIT_ROOM_PCT:
            return {
                "action": "HOLD", "side": side, "score": 0,
                "readiness_score": readiness_score,
                "readiness_components": readiness_components,
                "wait_estimate": "等待前高/前低空間擴大後重新評估",
                "profit_room_pct": profit_room_pct,
                "reason": (
                    f"獲利空間不足：目前{profit_room_pct:.2%}<"
                    f"最低{rules.MIN_ENTRY_PROFIT_ROOM_PCT:.2%}，拒絕低價值進場"
                ),
                **common,
            }
        prev_adx = float(prev["adx"]) if not rules.pd.isna(prev["adx"]) else 0.0
        macd_expanding = (
            macd_hist > 0 and macd_hist > prev_macd_hist
            if side == "LONG"
            else macd_hist < 0 and macd_hist < prev_macd_hist
        )
        volume_recovering = (
            volume_ratio >= rules.TREND_EXTENSION_MIN_VOLUME_RATIO
            and volume > float(prev["volume"])
        )
        is_trend_extension = (
            profit_room_pct >= rules.TREND_EXTENSION_MIN_ROOM_PCT
            and adx > prev_adx
            and macd_expanding
            and volume_recovering
            and candle_body_atr >= rules.TREND_EXTENSION_MIN_BODY_ATR_MULT
        )
        profit_profile = "TREND_EXTENSION" if is_trend_extension else "BOUNCE"
        profit_profile_label = "趨勢延伸" if is_trend_extension else "反彈單"
        is_bounce = profit_profile == "BOUNCE"
        bounce_capture_ratio = candidate_capture_ratio if is_bounce else 0.0
        bounce_target_pct = profit_room_pct * bounce_capture_ratio
        profit_exit_note = (
            f"預定收割{bounce_capture_ratio:.0%}於{bounce_target_pct:.2%}"
            if is_bounce else "採動態峰值停利"
        )
        rsi_arrow = "↑" if side == "LONG" else "↓"
        return {
            "action": "ENTER_LIMIT", "entry_mode": "SUPPORT_PULLBACK", "score": score,
            "readiness_score": 100, "readiness_components": readiness_components,
            "wait_estimate": "條件已完成，等待掛單成交",
            "target_price": target_price,
            "profit_profile": profit_profile,
            "profit_room_pct": profit_room_pct,
            "bounce_capture_ratio": bounce_capture_ratio,
            "bounce_target_pct": bounce_target_pct,
            "high_readiness_low_room": high_readiness_low_room,
            "reason": (
                f"SupportPullback_{side}｜{entry_support_name}{reversal_desc}｜"
                f"Maker@{target_price:.6g}｜量能{volume_ratio:.2f}x｜RSI={rsi:.1f}{rsi_arrow}｜"
                f"{memory_note}｜"
                f"{profit_profile_label}｜可用空間{profit_room_pct:.2%}｜"
                f"{profit_exit_note}"
                + (
                    "｜滿準備度低空間探索小倉"
                    if high_readiness_low_room else ""
                )
                + (
                    f"｜BTC反向扣{btc_score_penalty}分、半倉"
                    if btc_contrary else ""
                )
            ),
            **common,
        }

    missing = []
    if not aligned:
        missing.append("5m/1h趨勢同向")
    if not near_support:
        missing.append(
            f"靠近{support_name}（目前{support_distance_atr:.2f}ATR，需≤{rules.STRUCTURED_SUPPORT_NEAR_ATR:.2f}）"
        )
    if not confirmation_recent:
        missing.append(
            f"近{rules.SUPPORT_PULLBACK_CONFIRM_MEMORY_BARS}根缺反轉K/MACD改善＋足夠實體"
        )
    if not volume_healthy:
        missing.append(
            f"量能（目前{volume_ratio:.3f}x，需{rules.SUPPORT_PULLBACK_MIN_VOLUME_RATIO:.2f}–{rules.SUPPORT_PULLBACK_MAX_VOLUME_RATIO:.2f}x）"
        )
    if not rsi_ok:
        rsi_target = (
            rules.SUPPORT_PULLBACK_RSI_LONG_MIN
            if side == "LONG" else rules.SUPPORT_PULLBACK_RSI_SHORT_MAX
        )
        rsi_arrow = "上升" if side == "LONG" else "下降"
        missing.append(f"RSI達{rsi_target:g}且{rsi_arrow}（目前{rsi:.1f}）")
    if not quality_ok:
        missing.append(f"ADX/ATR品質（ADX {adx:.1f}，ATR {atr_pct:.2%}）")

    if not aligned or not quality_ok:
        wait_estimate = "趨勢或品質未通過，暫時無法估時"
    else:
        distance_bars = rules.math.ceil(
            max(0.0, support_distance_atr - rules.STRUCTURED_SUPPORT_NEAR_ATR) / 0.25
        )
        estimate_bars = min(12, max(1, distance_bars))
        wait_estimate = (
            f"最快約{estimate_bars * 5}–{(estimate_bars + 1) * 5}分鐘"
            f"（至少{estimate_bars}根5m收盤，僅估計）"
        )
    missing_text = "、".join(missing[:4])
    if len(missing) > 4:
        missing_text += f"，另{len(missing) - 4}項"
    if btc_contrary:
        missing_text += (
            f"｜BTC方向相反僅扣{btc_score_penalty}分、"
            f"倉位×{btc_allocation_factor:.2f}，不阻擋"
        )

    return {
        "action": "HOLD", "side": side, "score": 0,
        "readiness_score": readiness_score,
        "readiness_components": readiness_components,
        "wait_estimate": wait_estimate,
        "reason": f"尚缺：{missing_text}｜{wait_estimate}", **common,
    }
