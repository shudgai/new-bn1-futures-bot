"""Revalidate a pending pullback using the latest strategy snapshot."""

from typing import Any

import pandas as pd


def confirm_pullback_entry(
    strategy: Any,
    rules: Any,
    df: pd.DataFrame,
    side: str,
    ema_1h: float | None = None,
    trend_1h_declining: bool = False,
    btc_st_direction_1h: int = 0,
    btc_st_flip_age: int = 999,
    symbol: str | None = None,
) -> dict:
    """回踩觸發當下的二次確認。

    訊號登記等待回踩時，可能已經是將近 PULLBACK_TIMEOUT_MINUTES 分鐘前
    （目前預設 20 秒）的舊資料；等價格真的回踩到目標區時，量能可能已經萎縮、RSI 可能已經
    轉弱、甚至大趨勢或 SuperTrend 方向已經反轉——這正是「假突破」最常見的
    樣貌：一開始的突破訊號成立，但等真正要進場時動能其實已經退潮。這裡用
    當下最新的一根 K 棒重新檢查核心條件是否還成立，任何一項不成立就直接
    取消這次回踩進場，而不是機械式地照著已經過期的舊訊號開倉。
    """
    if len(df) < 50:
        return {"status": "CANCEL", "reason": "K線資料不足"}

    df = strategy.compute_indicators(df)
    curr = df.iloc[-1]
    price = curr['close_price_spike_filtered'] if ('close_price_spike_filtered' in curr and not rules.pd.isna(curr['close_price_spike_filtered'])) else curr['close']
    atr = curr['atr'] if not rules.np.isnan(curr['atr']) else price * 0.015
    rsi = curr['rsi']
    vol = curr['volume']
    vol_ma_20 = curr['vol_ma_20'] if not rules.np.isnan(curr['vol_ma_20']) else 0
    ema_20 = curr['ema_20'] if not rules.pd.isna(curr['ema_20']) else price
    st_dir = curr['st_direction']
    want_dir = 1 if side == "LONG" else -1

    if st_dir != want_dir:
        return {
            "status": "CANCEL",
            "reason": f"SuperTrend 方向已反轉（現為{'多頭' if st_dir == 1 else '空頭'}）",
        }

    btc_regime = rules.classify_btc_regime(
        want_dir, btc_st_direction_1h, btc_st_flip_age, symbol=symbol
    )
    if btc_regime["hard_block"]:
        reason = (
            "BTC 1h 方向背離，禁止逆大盤進場"
            if btc_regime["mode"] == "CONTRARY"
            else f"BTC 1h 剛翻轉，仍在 {btc_st_flip_age}/{rules.BTC_REGIME_FLIP_BUFFER_BARS} 根緩衝期"
        )
        return {"status": "CANCEL", "reason": reason}

    if side == "LONG" and rsi > rules.RSI_LONG_MAX:
        return {"status": "CANCEL", "reason": f"RSI過熱 RSI_Overbought({rsi:.1f}>{rules.RSI_LONG_MAX:.1f})"}
    if side == "SHORT" and rsi < rules.RSI_SHORT_MIN:
        return {"status": "CANCEL", "reason": f"RSI過冷 RSI_Oversold({rsi:.1f}<{rules.RSI_SHORT_MIN:.1f})"}

    # 大週期（1h）本身動能衰退時，即使 5 分K條件仍成立也取消。
    if trend_1h_declining:
        return {
            "status": "CANCEL",
            "reason": "大週期(1h)動能已在衰退 1h_Trend_Declining",
        }

    # 價格乖離 EMA30 太遠：等回踩的這段時間裡價格可能又衝更遠，均值
    # 回歸風險比登記當下更高，一樣取消。
    ema30_distance_atr = abs(price - ema_20) / atr if atr > 0 else 0.0
    if ema30_distance_atr > rules.EMA_EXTENSION_MAX_ATR_MULT:
        return {
            "status": "CANCEL",
            "reason": f"價格乖離EMA30過大 Price_Overextended({ema30_distance_atr:.1f}x_ATR)",
        }

    # 回踩跌破/突破 EMA30：健康的回調應該只是往 EMA30 靠近，不會真的
    # 穿越到對面——多單回踩時價格已經跌破 EMA30（或空單回踩時已經站
    # 上 EMA30），代表這已經不是「回調」，而是價格真的穿越均線在反轉。
    # 跟上面「乖離過大」是兩個不同方向的風險：那個抓「離 EMA30 太遠」
    # （不管在哪一側），這個抓「跑到 EMA30 錯的那一側」，兩種情況都
    # 可能發生、必須分開判斷，缺一不可。
    if side == "LONG" and price < ema_20:
        return {
            "status": "CANCEL",
            "reason": f"回踩跌破EMA30，疑似真反轉 EMA30_Breached(price={price:.6f}<ema30={ema_20:.6f})",
        }
    if side == "SHORT" and price > ema_20:
        return {
            "status": "CANCEL",
            "reason": f"回踩突破EMA30，疑似真反轉 EMA30_Breached(price={price:.6f}>ema30={ema_20:.6f})",
        }

    # 距離原始突破過了多久：方向沒反轉不代表這個突破還「新鮮」——等回踩
    # 的這段時間裡，行情可能只是在原地震盪消耗動能，SuperTrend 遲遲沒
    # 真的翻轉，但這個突破本身已經是強弩之末。跟 evaluate_signal() 的
    # 新鮮度用同一套連續淡化公式重新算一次，太舊直接取消，不是只看
    # 方向對不對。
    st_flip_age = rules.bars_since_supertrend_flip(df['st_direction'])
    freshness_ratio = max(0.0, 1.0 - st_flip_age / rules.FRESHNESS_DECAY_BARS) if rules.FRESHNESS_DECAY_BARS > 0 else 0.0
    freshness_score = round(freshness_ratio * 30)
    if freshness_score < rules.MIN_FRESHNESS_SCORE:
        return {
            "status": "CANCEL",
            "reason": f"距離原始突破已過太久 Freshness({st_flip_age}bars)={freshness_score}<{rules.MIN_FRESHNESS_SCORE}",
        }

    # ADX 動能衰退檢查（跟 evaluate_signal() 同一套邏輯）：方向沒反轉、
    # 新鮮度也還夠，但 ADX 現在比 N 根K棒前低且已經低於
    # WEAK_ENERGY_ADX_THRESHOLD，代表動能在等待回踩的這段時間持續
    # 衰退，一樣是末端趨勢的樣貌。
    adx = curr['adx'] if not rules.pd.isna(curr['adx']) else 0.0
    adx_lookback_idx = len(df) - 1 - rules.ADX_DECLINE_LOOKBACK_BARS
    adx_prior = df['adx'].iloc[adx_lookback_idx] if adx_lookback_idx >= 0 else rules.np.nan
    adx_drop = (adx_prior - adx) if not rules.pd.isna(adx_prior) else 0.0
    adx_declining = (
        not rules.pd.isna(adx_prior)
        and adx_drop >= max(rules.ADX_DECLINE_MIN_DROP, adx_prior * rules.ADX_DECLINE_MIN_DROP_RATIO)
    )
    adx_declining_exhausted = adx_declining and adx < rules.WEAK_ENERGY_ADX_THRESHOLD
    if adx_declining_exhausted:
        return {
            "status": "CANCEL",
            "reason": (
                f"ADX 動能持續衰退 {adx:.1f}<{adx_prior:.1f}"
                f"（{rules.ADX_DECLINE_LOOKBACK_BARS}根K棒前）且低於能量門檻 {rules.WEAK_ENERGY_ADX_THRESHOLD:.1f}"
            ),
        }

    if ema_1h is not None:
        if side == "LONG" and price < ema_1h:
            return {"status": "CANCEL", "reason": "1h 大趨勢已轉空"}
        if side == "SHORT" and price > ema_1h:
            return {"status": "CANCEL", "reason": "1h 大趨勢已轉多"}

    atr_pct = atr / price if price > 0 else 0
    if atr_pct > rules.MAX_ATR_PCT:
        return {"status": "CANCEL", "reason": f"波動率轉為過高 ATR_Too_High({atr_pct:.2%})"}
    if atr_pct < rules.MIN_ATR_PCT:
        return {"status": "CANCEL", "reason": f"波動率轉為過低 ATR_Too_Low({atr_pct:.2%})"}

    # 回踩縮量使用多空合計成交量，無法辨識是順勢量或逆勢量；尤其
    # 回踩時縮量常屬正常型態，因此只讓量能項記 0 分，不再硬取消。
    volume_faded = False
    recent_vol_avg = None
    min_sustain_vol = None
    if vol_ma_20 > 0 and len(df) >= 3:
        recent_vol_avg = float(df['volume'].iloc[-3:-1].mean())
        min_sustain_vol = float(vol_ma_20 * rules.POST_BREAKOUT_VOL_SUSTAIN_RATIO)
        volume_faded = recent_vol_avg < min_sustain_vol

    # 逆向爆量先觀察、不硬擋：多單的大陰K或空單的大陽K若達均量1.8倍，
    # 回傳旗標讓引擎記錄，日後可用真實結果決定是否升級為硬性撤單。
    candle_open = float(curr.get('open', curr['close']))
    candle_close = float(curr['close'])
    volume_ratio = (vol / vol_ma_20) if vol_ma_20 > 0 else 0.0
    adverse_candle = (
        (side == "LONG" and candle_close < candle_open)
        or (side == "SHORT" and candle_close > candle_open)
    )
    adverse_volume_spike = (
        adverse_candle
        and abs(candle_close - candle_open) >= atr * rules.ADVERSE_PULLBACK_BODY_MIN_ATR_MULT
        and volume_ratio >= rules.ADVERSE_PULLBACK_VOLUME_SPIKE_RATIO
    )

    # 回調總分（B量能+C RSI+D新鮮度+E品質加分，滿分79，跟 evaluate_signal()
    # 同一套加權）：量能/RSI 不再各自當硬性關卡、任一項不過就整筆取消，
    # 改成允許互相補償——量能爆量成長可以補足 RSI 差一點點的缺口，更貼近
    # 真實交易判斷。上面的方向反轉/大趨勢衰退/新鮮度太舊/ADX動能衰退/
    # 價格乖離過大/ATR%範圍 是絕對紅線，不受總分補償影響，維持硬性取消。
    score_b = (
        20
        if not volume_faded
        and vol_ma_20 > 0
        and vol >= vol_ma_20 * rules.KELTNER_MIN_VOLUME_RATIO
        else 0
    )
    score_c = 20 if (
        (side == "LONG" and rsi >= rules.RSI_LONG_THRESHOLD)
        or (side == "SHORT" and rsi <= rules.RSI_SHORT_THRESHOLD)
    ) else 0
    score_d = freshness_score

    atr_mid = (rules.MIN_ATR_PCT + rules.MAX_ATR_PCT) / 2.0
    atr_half_range = (rules.MAX_ATR_PCT - rules.MIN_ATR_PCT) / 2.0
    atr_quality = (
        max(0.0, 1.0 - abs(atr_pct - atr_mid) / atr_half_range)
        if atr_half_range > 0 else 0.0
    )
    rsi_ideal = 60.0 if side == "LONG" else 40.0
    rsi_half_width = max(
        rsi_ideal - rules.RSI_LONG_THRESHOLD if side == "LONG" else rules.RSI_SHORT_THRESHOLD - rsi_ideal,
        1.0,
    )
    rsi_quality = max(0.0, 1.0 - abs(rsi - rsi_ideal) / rsi_half_width)
    vol_ratio = (vol / vol_ma_20) if vol_ma_20 > 0 else 0.0
    vol_margin = max(0.0, vol_ratio - rules.KELTNER_MIN_VOLUME_RATIO)
    adx_ratio = (adx - rules.ADX_QUALITY_MIN) / (rules.ADX_QUALITY_FULL - rules.ADX_QUALITY_MIN)
    score_e = (
        round(atr_quality * 3)
        + round(rsi_quality * 3)
        + round(min(vol_margin / 1.0, 1.0) * 3)
        + round(min(max(adx_ratio, 0.0), 1.0) * 3)
    )
    adx_decline_soft_penalty = 1 if adx_declining and not adx_declining_exhausted else 0
    if adx_decline_soft_penalty:
        score_e = max(0, score_e - adx_decline_soft_penalty)

    if score_e < rules.ENTRY_MIN_QUALITY_BONUS:
        return {
            "status": "CANCEL",
            "reason": f"回調品質不足 Quality_Too_Low({score_e}<{rules.ENTRY_MIN_QUALITY_BONUS})",
            "adverse_volume_spike": adverse_volume_spike,
            "adverse_volume_ratio": volume_ratio,
        }

    raw_pullback_score = score_b + score_c + score_d + score_e
    pullback_score = max(0, raw_pullback_score - btc_regime["score_penalty"])
    if pullback_score < rules.PULLBACK_SCORE_THRESHOLD:
        return {
            "status": "CANCEL",
            "raw_pullback_score": raw_pullback_score,
            "pullback_score": pullback_score,
            "volume_faded": volume_faded,
            "recent_volume_avg": recent_vol_avg,
            "min_sustain_volume": min_sustain_vol,
            "adverse_volume_spike": adverse_volume_spike,
            "adverse_volume_ratio": volume_ratio,
            "reason": (
                f"回調總分不足 Pullback_Score({pullback_score}<{rules.PULLBACK_SCORE_THRESHOLD}) | "
                f"Volume+{score_b} RSI+{score_c} Freshness+{score_d} Quality+{score_e} "
                f"BTC-{btc_regime['score_penalty']}"
            ),
        }

    return {
        "status": "PASS",
        "reason": (
            f"二次確認通過 Pullback_Score({pullback_score})"
            + (
                f" | ADX {adx:.1f}←{adx_prior:.1f}仍高於{rules.WEAK_ENERGY_ADX_THRESHOLD:g}，品質-1"
                if adx_decline_soft_penalty else ""
            )
        ),
        "raw_pullback_score": raw_pullback_score,
        "pullback_score": pullback_score,
        "volume_faded": volume_faded,
        "recent_volume_avg": recent_vol_avg,
        "min_sustain_volume": min_sustain_vol,
        "adverse_volume_spike": adverse_volume_spike,
        "adverse_volume_ratio": volume_ratio,
        "btc_regime_mode": btc_regime["mode"],
        "btc_direction_1h": int(btc_st_direction_1h or 0),
        "btc_score_penalty": btc_regime["score_penalty"],
        "btc_allocation_factor": btc_regime["allocation_factor"],
    }
