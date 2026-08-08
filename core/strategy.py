import math
import pandas as pd
import numpy as np
from core.config import (
    STOP_LOSS_MULTIPLIER, TAKE_PROFIT_MULTIPLIER, TAKER_FEE_RATE, MIN_NET_REWARD_RISK,
    KELTNER_BREAKOUT_MARGIN_PCT, KELTNER_MIN_VOLUME_RATIO, FRESHNESS_DECAY_BARS,
    ENTRY_FRESHNESS_SCORE_MAX,
    MIN_FRESHNESS_SCORE,
    RSI_LONG_THRESHOLD, RSI_SHORT_THRESHOLD, RSI_LONG_MAX, RSI_SHORT_MIN,
    MIN_SCORE_THRESHOLD, ENTRY_MIN_QUALITY_BONUS, PULLBACK_ZONE_PCT, MAX_ATR_PCT, MIN_ATR_PCT,
    KELTNER_ATR_MULTIPLIER, get_pullback_target_depth, MIN_SL_DISTANCE_PCT,
    ADX_PERIOD, ADX_QUALITY_MIN, ADX_QUALITY_FULL, ADX_DECLINE_LOOKBACK_BARS,
    WEAK_ENERGY_ADX_THRESHOLD,
    ADX_DECLINE_MIN_DROP, ADX_DECLINE_MIN_DROP_RATIO,
    EMA_EXTENSION_MAX_ATR_MULT, PULLBACK_SCORE_THRESHOLD, DISASTER_STOP_MULTIPLIER,
    ADX_MANDATORY_MIN, BREAKOUT_CONFIRM_BARS, POST_BREAKOUT_VOL_SUSTAIN_RATIO,
    PULLBACK_TARGET_MIN_ATR_MULT, ADVERSE_PULLBACK_VOLUME_SPIKE_RATIO,
    ADVERSE_PULLBACK_BODY_MIN_ATR_MULT,
    TREND_AGREE_EMA_MARGIN_PCT,
    BTC_REGIME_FILTER_ENABLED, BTC_REGIME_ALLOW_CONTRARY,
    BTC_REGIME_FLIP_BUFFER_BARS, BTC_REGIME_SCORE_PENALTY,
    BTC_REGIME_ALLOCATION_FACTOR,
    MA7_EARLY_ENTRY_ENABLED, MA7_EARLY_MIN_ATR_MULT, MA7_REVERSAL_MIN_ATR_MULT,
    MA7_FAST_ENTRY_ENABLED, MA7_FAST_MIN_ATR_MULT, MA7_FAST_MAX_ATR_MULT,
    MA7_FAST_MIN_VOLUME_RATIO, MA7_DYNAMIC_ATR_FLOOR_PCT,
    MA7_BOTTOM_ENTRY_ENABLED, MA7_BOTTOM_OFFSET_ATR_MULT,
    STRUCTURED_VOLUME_MIN_RATIO, STRUCTURED_SWING_LOOKBACK,
    STRUCTURED_SUPPORT_NEAR_ATR, STRUCTURED_RSI_LONG_TRIGGER,
    STRUCTURED_RSI_SHORT_TRIGGER, ENABLE_MOMENTUM_CROSS_ENTRY, ENABLE_BREAKOUT_ENTRY,
    ENABLE_1H_EMA50_FILTER,
)
from core.indicators import bars_since_supertrend_flip
from core.config import (
    ADX_DECLINE_LOOKBACK_BARS, ADX_DECLINE_MIN_DROP, ADX_DECLINE_MIN_DROP_RATIO,
    KC_TOUCH_LOOKBACK_BARS,
    MAINSTREAM_SYMBOLS, VOLUME_DIVERGENCE_LOOKBACK_BARS, VOLUME_DIVERGENCE_MAX_RATIO,
)


def has_volume_divergence(df: pd.DataFrame, want_dir: int) -> bool:
    """價格仍創新高/新低，但成交量較前段明顯萎縮 -> 量縮背離（主力收手）。

    把最近 VOLUME_DIVERGENCE_LOOKBACK_BARS 根拆成前後兩半：
      多單（探底）：後半段的最低價 <= 前半段最低價，但後半段平均量能
      明顯低於前半段 -> 底部量縮，賣壓竭盡。
      空單（探頂）：後半段的最高價 >= 前半段最高價，但後半段平均量能
      明顯低於前半段 -> 頂部量縮，買盤竭盡。
    """
    if len(df) < VOLUME_DIVERGENCE_LOOKBACK_BARS:
        return False
    window = df.iloc[-VOLUME_DIVERGENCE_LOOKBACK_BARS:]
    half = VOLUME_DIVERGENCE_LOOKBACK_BARS // 2
    early, recent = window.iloc[:half], window.iloc[half:]
    early_volume = float(early['volume'].mean())
    if early_volume <= 0:
        return False
    volume_shrinking = float(recent['volume'].mean()) <= early_volume * VOLUME_DIVERGENCE_MAX_RATIO
    if not volume_shrinking:
        return False
    if want_dir == 1:
        return float(recent['low'].min()) <= float(early['low'].min())
    return float(recent['high'].max()) >= float(early['high'].max())

def compute_pullback_target(
    kc_edge: float, ema_20: float, atr: float, side: str, score: int
) -> tuple[float, float, bool]:
    """Return (target, pullback_distance, has_enough_room) using one shared rule."""
    depth = get_pullback_target_depth(score)
    span = abs(float(kc_edge) - float(ema_20))
    min_distance = max(0.0, float(atr) * PULLBACK_TARGET_MIN_ATR_MULT)
    if span + 1e-12 < min_distance:
        return float(kc_edge), span, False
    distance = min(span, max(span * depth, min_distance))
    target = (
        float(kc_edge) - distance
        if str(side).upper() == "LONG"
        else float(kc_edge) + distance
    )
    return target, distance, True


def classify_btc_regime(
    st_direction: int, btc_direction: int, flip_age: int, symbol: str = None,
    score_penalty: int = None,
) -> dict:
    """Return BTC alignment context and block contrary trades unless explicitly allowed."""
    context = {
        "mode": "UNKNOWN",
        "score_penalty": 0,
        "allocation_factor": 1.0,
        "hard_block": False,
    }
    if not BTC_REGIME_FILTER_ENABLED or btc_direction == 0:
        return context
    if flip_age < BTC_REGIME_FLIP_BUFFER_BARS:
        context.update(mode="JUST_FLIPPED", hard_block=True)
        return context
    if str(symbol or "").replace("/", "").upper() == "BTCUSDT":
        context["mode"] = "SELF"
        return context
    contrary = (st_direction == 1 and btc_direction == -1) or (
        st_direction == -1 and btc_direction == 1
    )
    if contrary:
        if not BTC_REGIME_ALLOW_CONTRARY:
            context.update(mode="CONTRARY", hard_block=True)
            return context
        context.update(
            mode="CONTRARY",
            score_penalty=(
                BTC_REGIME_SCORE_PENALTY if score_penalty is None else max(0, int(score_penalty))
            ),
            allocation_factor=BTC_REGIME_ALLOCATION_FACTOR,
        )
    else:
        context["mode"] = "ALIGNED"
    return context


def compute_sl_tp_distance(price: float, atr: float) -> tuple[float, float]:
    """算出止損/止盈距離，並套用 MIN_SL_DISTANCE_PCT 下限，避免低波動期間
    ATR 太小導致止損距離縮到容易被雜訊掃出的地步。回傳 (sl_distance, tp_distance)。

    止損可由 DISASTER_STOP_MULTIPLIER 放寬，但止盈會同步拉遠到「扣除
    進出場 taker fee 後」仍至少符合 MIN_NET_REWARD_RISK，避免表面 1:2、
    實際因放寬止損與手續費只剩約 1:1.3。公式使用較保守的較高出場名目
    金額估算手續費，因此多空方向都不會低於設定值。"""
    base_sl_distance = max(atr * STOP_LOSS_MULTIPLIER, price * MIN_SL_DISTANCE_PCT)
    sl_distance = base_sl_distance * DISASTER_STOP_MULTIPLIER
    configured_tp_distance = base_sl_distance * (
        TAKE_PROFIT_MULTIPLIER / STOP_LOSS_MULTIPLIER
    )
    fee_rate = max(0.0, min(TAKER_FEE_RATE, 0.99))
    net_risk_per_unit = sl_distance * (1 + fee_rate) + 2 * price * fee_rate
    min_tp_distance = (
        MIN_NET_REWARD_RISK * net_risk_per_unit + 2 * price * fee_rate
    ) / max(1 - fee_rate, 1e-9)
    tp_distance = max(configured_tp_distance, min_tp_distance)
    return sl_distance, tp_distance


def detect_ma7_reversal(
    df: pd.DataFrame,
    side: str,
    ema_50_1h: float = None,
    st_direction_1h: int = None,
    btc_st_direction_1h: int = 0,
    btc_st_flip_age: int = 999,
    symbol: str = None,
    parameter_overrides: dict = None,
    indicators_precomputed: bool = False,
    live_price: float = None,
) -> dict:
    """1m MA7 回撤底部預掛與谷底（多單）/峰頂（空單）拐頭偵測。

    觸發條件（以多單為例）：
      ma7[-3] > ma7[-2] and ma7[-1] > ma7[-2]
    即前一根 MA7 低於兩根前（確立谷底），且當前 MA7 已向上翻。

    需同時通過：
      - SuperTrend 方向對齊
      - 1h ST 方向對齊（若啟用）
      - ADX / ATR / RSI 基礎品質過濾
      - BTC 大盤守門員
      - KC 位置驗證：現價在 EMA20 同側（不能整個跑到通道另一邊）

    回傳 {"detected": True/False, "reason": str, ...}
    """
    if len(df) < 20:
        return {"detected": False, "reason": "K線資料不足"}

    if not indicators_precomputed:
        df = SuperTrendKeltnerStrategy().compute_indicators(df)

    overrides = dict(parameter_overrides or {})
    atr_min_pct = float(overrides.get("atr_min_pct", MIN_ATR_PCT))
    rsi_long_max = float(overrides.get("rsi_long_max", RSI_LONG_MAX))
    rsi_short_min = float(overrides.get("rsi_short_min", RSI_SHORT_MIN))

    curr = df.iloc[-1]
    closed_price = (
        curr['close_price_spike_filtered']
        if ('close_price_spike_filtered' in curr and not pd.isna(curr['close_price_spike_filtered']))
        else curr['close']
    )
    price = float(live_price) if live_price is not None and float(live_price) > 0 else float(closed_price)
    atr = curr['atr'] if not np.isnan(curr['atr']) else price * 0.015
    rsi = curr['rsi']
    adx = curr['adx'] if not np.isnan(curr['adx']) else 0.0
    vol = curr['volume']
    vol_ma_20 = curr['vol_ma_20'] if not np.isnan(curr['vol_ma_20']) else 0
    ema_20 = curr['ema_20'] if not pd.isna(curr['ema_20']) else price
    kc_upper = curr['kc_upper']
    kc_lower = curr['kc_lower']
    st_dir = int(curr['st_direction'])
    want_dir = 1 if str(side).upper() == "LONG" else -1

    # 提前計算品質分數（用於狀態待命時顯示預估分數，上限 89 避免誤觸 CURRENT_MAKER 路徑）
    score = 65  # 固定評分基準；不得隨開倉門檻上調而灌高訊號分數
    if vol_ma_20 > 0 and vol >= vol_ma_20 * KELTNER_MIN_VOLUME_RATIO:
        score += 10  # 量能確認
    if want_dir == 1 and rsi >= RSI_LONG_THRESHOLD:
        score += 5
    elif want_dir == -1 and rsi <= RSI_SHORT_THRESHOLD:
        score += 5
    adx_ratio = (adx - ADX_MANDATORY_MIN) / max(ADX_QUALITY_FULL - ADX_MANDATORY_MIN, 1.0)
    score += round(min(max(adx_ratio, 0.0), 1.0) * 9)  # ADX 品質最多 +9
    score = min(score, 89)

    def _no(reason: str) -> dict:
        return {"detected": False, "reason": reason, "side": side, "score": score}

    # SuperTrend 方向對齊
    if st_dir != want_dir:
        return _no(f"SuperTrend方向不符（{st_dir}≠{want_dir}）")

    # 1h SuperTrend 方向檢查已禁用，允許逆勢進場以增加開倉機會

    # BTC 大盤守門員
    btc_regime = classify_btc_regime(
        st_dir, btc_st_direction_1h, btc_st_flip_age, symbol=symbol,
    )
    if btc_regime["hard_block"]:
        if btc_regime["mode"] == "CONTRARY":
            return _no("BTC 1h方向背離，禁止逆大盤進場")
        return _no(f"BTC_JustFlipped({btc_st_flip_age}bars)")

    # 1h EMA50 方向檢查已禁用，允許逆勢進場

    # ADX 硬性最低門檻 (動態調整：若 1h 趨勢對齊，放寬至 8.0)
    dynamic_adx_min = ADX_MANDATORY_MIN
    if st_direction_1h == want_dir:
        dynamic_adx_min = max(8.0, ADX_MANDATORY_MIN - 2.0)
    if adx < dynamic_adx_min:
        return _no(f"ADX太低({adx:.1f}<{dynamic_adx_min})")

    # SuperTrend 翻轉後已過根數 — 防趨勢尾部進場
    # FRESHNESS_DECAY_BARS 是新鮮度衰減到 0 的根數上限，若超過其 70%
    # 代表這根 ST 翻轉已相當陳舊，MA7 拐頭很可能只是尾部震盪，不再進場。
    st_flip_age = bars_since_supertrend_flip(df['st_direction'])
    max_allowed_flip_age = int(FRESHNESS_DECAY_BARS * 0.70)
    if st_flip_age > max_allowed_flip_age:
        return _no(f"SuperTrend翻轉過舊({st_flip_age}根>{max_allowed_flip_age}根)，趨勢尾部不進場")

    # ADX 衰退且已低於能量門檻 — 動能退潮，硬性擋單（與主路徑 ADX_DECLINING_EXHAUSTED 對齊）。
    # 絕對門檻原本用 ADX_QUALITY_MIN(15)，但實測 ONDO/USDT 這筆 ADX 從
    # 36.3 一路衰退到 20 左右進場，衰退幅度很明顯（跌43%）卻因為還沒
    # 低於15分而完全沒被擋到，進場後就遇到窄幅雜訊盤整停損。改用
    # WEAK_ENERGY_ADX_THRESHOLD(22)，跟槓桿封頂門檻共用同一套「能量」
    # 標準，衰退到這個中等能量區間就直接擋單，不再只是降槓桿了事。
    adx_lookback_idx = len(df) - 1 - ADX_DECLINE_LOOKBACK_BARS
    adx_prior = df['adx'].iloc[adx_lookback_idx] if adx_lookback_idx >= 0 else float('nan')
    adx_drop = (float(adx_prior) - float(adx)) if not math.isnan(float(adx_prior)) else 0.0
    adx_declining_exhausted = (
        not math.isnan(float(adx_prior))
        and adx_drop >= max(ADX_DECLINE_MIN_DROP, float(adx_prior) * ADX_DECLINE_MIN_DROP_RATIO)
        and adx < WEAK_ENERGY_ADX_THRESHOLD
    )
    if adx_declining_exhausted:
        return _no(f"ADX動能衰退({adx:.1f}<{WEAK_ENERGY_ADX_THRESHOLD},跌{adx_drop:.1f})，趨勢尾部不進場")

    # ATR 波動範圍
    atr_pct = atr / price if price > 0 else 0
    if atr_pct > MAX_ATR_PCT:
        return _no(f"ATR過高({atr_pct:.2%})")
    
    # 計算最近 6 小時（72 根 5m K棒）的平均 ATR% 作為動態底線參考。
    # 絕對下限由設定控制，讓低波動期可適度放寬，但仍排除幾乎無波動的雜訊。
    atr_pct_series = df['atr'] / df['close']
    rolling_atr_pct = float(atr_pct_series.rolling(window=72, min_periods=12).mean().iloc[-1])
    dynamic_atr_min = min(
        atr_min_pct,
        max(MA7_DYNAMIC_ATR_FLOOR_PCT, rolling_atr_pct * 0.7),
    )
    is_contrarian_bottom_buy = False
    if atr_pct < dynamic_atr_min:
        # 主流幣量縮背離例外：波動雖低，但價格仍創新高/新低、量能卻明顯
        # 萎縮，代表主力收手動能耗盡準備反轉，不是無動能的雜訊盤整，
        # 允許繞過波動過低限制（僅此一項，其餘過濾條件不受影響）。
        if symbol in MAINSTREAM_SYMBOLS and has_volume_divergence(df, want_dir):
            pass
        else:
            # 逆勢承接（MA7_ContrarianBottomBuy）已停用：實測17%勝率、
            # 12筆虧損7.18U，就算加上量能確認/縮小倉位/2根K棒確認等風控，
            # 依然是跟1h趨勢對作，方向判斷本身不準的問題無法靠風控修正。
            # 保留 is_contrarian_bottom_buy 相關的下游程式碼（分數/倉位/
            # 移動停利觸發門檻），未來若要重新啟用只需在這裡恢復翻轉邏輯。
            return _no(f"ATR過低({atr_pct:.2%}<{dynamic_atr_min:.2%})")

    # RSI 過熱/過冷
    if want_dir == 1 and rsi > rsi_long_max:
        return _no(f"RSI過熱({rsi:.1f}>{rsi_long_max:.1f})")
    if want_dir == -1 and rsi < rsi_short_min:
        return _no(f"RSI過冷({rsi:.1f}<{rsi_short_min:.1f})")

    # ── 結合方案：突破 Keltner 通道 (KC) + MACD/RSI 動能指標偏強 ──
    # 1. 價格突破 KC 軌道
    is_breakout = False
    if want_dir == 1:
        if price > kc_upper:
            is_breakout = True
        else:
            return _no(f"價格未突破KC上軌（{price:.6g}<={kc_upper:.6g}）")
    else:
        if price < kc_lower:
            is_breakout = True
        else:
            return _no(f"價格未突破KC下軌（{price:.6g}>={kc_lower:.6g}）")

    # 2. MACD 零軸上方黃金交叉 / 多頭動能確認
    if 'macd_line' not in df.columns:
        return _no("MACD指標未計算")
    macd_line = float(curr['macd_line'])
    macd_signal = float(curr['macd_signal'])
    macd_hist = float(curr['macd_hist'])

    macd_ok = False
    if want_dir == 1:
        # MACD 黃金交叉且柱狀體為正
        if macd_line > macd_signal and macd_hist > 0:
            macd_ok = True
        else:
            return _no(f"MACD未呈現多頭動能（DIF={macd_line:.6g}, DEA={macd_signal:.6g}, HIST={macd_hist:.6g}）")
    else:
        # MACD 死亡交叉且柱狀體為負
        if macd_line < macd_signal and macd_hist < 0:
            macd_ok = True
        else:
            return _no(f"MACD未呈現空頭動能（DIF={macd_line:.6g}, DEA={macd_signal:.6g}, HIST={macd_hist:.6g}）")

    # 3. RSI 突破 50 (多頭動能確認)
    rsi_ok = False
    if want_dir == 1:
        if rsi >= 50.0:
            rsi_ok = True
        else:
            return _no(f"RSI低於50（RSI={rsi:.1f}）")
    else:
        if rsi <= 50.0:
            rsi_ok = True
        else:
            return _no(f"RSI高於50（RSI={rsi:.1f}）")

    # 4. 成交量放大 (Volume Ratio >= 1.0)
    volume_ratio = float(vol / vol_ma_20) if vol_ma_20 > 0 else 0.0
    if volume_ratio < 1.0:
        return _no(f"成交量未放大（Volume Ratio={volume_ratio:.2f}<1.0）")

    # 所有核心條件均通過！開始設定訊號評分
    score = 72  # 基礎分高於 MIN_SCORE_THRESHOLD (71)
    if volume_ratio >= 1.5:
        score += 10
    if want_dir == 1 and rsi >= 58.0:
        score += 7
    elif want_dir == -1 and rsi <= 42.0:
        score += 7
    adx_ratio = (adx - ADX_MANDATORY_MIN) / max(ADX_QUALITY_FULL - ADX_MANDATORY_MIN, 1.0)
    score += round(min(max(adx_ratio, 0.0), 1.0) * 10)  # ADX 品質最多 +10
    score = min(score, 89)

    # 計算結構性止損位（參考過去 6 根已收盤 K 棒的波段高低點與 KC 軌道，外加 0.05 * ATR 緩衝避免精準掃單）
    past_6_bars = df.iloc[-7:-1]
    if want_dir == 1:
        swing_low = float(past_6_bars['low'].min())
        kc_lower_val = float(past_6_bars['kc_lower'].min())
        structural_sl = min(swing_low, kc_lower_val) - 0.05 * atr
    else:
        swing_high = float(past_6_bars['high'].max())
        kc_upper_val = float(past_6_bars['kc_upper'].max())
        structural_sl = max(swing_high, kc_upper_val) + 0.05 * atr

    # 為了向下相容，填入對應的 MA7 變數
    ma7_series = df['ma7'].dropna()
    ma7_curr = float(ma7_series.iloc[-1]) if len(ma7_series) > 0 else price
    ma7_prev = float(ma7_series.iloc[-2]) if len(ma7_series) > 1 else price
    ma7_prev2 = float(ma7_series.iloc[-3]) if len(ma7_series) > 2 else price

    direction_note = "突破KC上軌+MACD金叉+RSI>=50" if want_dir == 1 else "跌破KC下軌+MACD死叉+RSI<=50"
    return {
        "detected": True,
        "side": side,
        "score": score,
        "price": float(price),
        "atr": float(atr),
        "ema_20": float(ema_20),
        "kc_upper": float(kc_upper),
        "kc_lower": float(kc_lower),
        "ma7_curr": ma7_curr,
        "ma7_prev": ma7_prev,
        "ma7_prev2": ma7_prev2,
        "ma7_projected": None,
        "early_projection": False,
        "fast_entry": False,
        "pullback_bottom_order": False,
        "entry_mode": "BREAKOUT_MOMENTUM",
        "target_price": None,
        "volume_ratio": volume_ratio,
        "rsi": float(rsi),
        "adx": float(adx),
        "btc_regime_mode": btc_regime["mode"],
        "btc_allocation_factor": btc_regime["allocation_factor"],
        "structural_sl": structural_sl,
        "is_contrarian_bottom_buy": False,
        "reason": (
            f"Breakout_Momentum_{side}｜"
            f"價格突破通道｜MACD黃金交叉｜RSI強勢｜"
            + f"Volume Ratio={volume_ratio:.2f}｜"
            + f"{direction_note}｜score={score}"
        ),
    }


class SuperTrendKeltnerStrategy:
    """
    高精度量化引擎 - 回調狙擊版本 (Pullback Sniper Mode)
    核心邏輯：
    1. 底線防禦 (Mandatory)：大週期趨勢 (1h EMA50) 與 SuperTrend 方向必須一致。
    2. 動態評分 (Scoring)：Keltner 突破、量能、RSI、訊號新鮮度 進行加權評分。
    3. 90+ 使用現價 Post-Only Maker；其餘達標訊號按分數等待回踩與二次確認。
    KC 突破後動量可能已接近末段，不因分數高而在突破高點市價追入。
    """
    def __init__(self, atr_period=10, atr_multiplier=3.0, adx_period=ADX_PERIOD):
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.adx_period = adx_period

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        high = df['high']
        low = df['low']

        # 防插針價格選擇
        if 'close_price_spike_filtered' in df.columns:
            close = df['close_price_spike_filtered'].fillna(df['close'])
        else:
            close = df['close']

        volume = df['volume']

        # ATR 計算
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=self.atr_period).mean()

        # EMAs
        df['ema_20'] = close.ewm(span=20, adjust=False).mean()
        df['ema_50'] = close.ewm(span=50, adjust=False).mean()

        # MA7
        df['ma7'] = close.rolling(window=7).mean()

        # 成交量均線
        df['vol_ma_20'] = volume.rolling(window=20).mean()

        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        df['rsi'] = 100 - (100 / (1 + rs))

        # MACD
        fast_ema = close.ewm(span=12, adjust=False).mean()
        slow_ema = close.ewm(span=26, adjust=False).mean()
        df['macd_line'] = fast_ema - slow_ema
        df['macd_signal'] = df['macd_line'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd_line'] - df['macd_signal']

        # ADX（趨勢強度濾網）：KC 突破配上低 ADX，是盤整期假突破的常見樣貌，
        # 用來在評分裡分辨「真的有趨勢動能撐著的突破」跟「雜訊型突破」。
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
        tr_smooth = tr.ewm(alpha=1 / self.adx_period, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(alpha=1 / self.adx_period, adjust=False).mean() / (tr_smooth + 1e-9))
        minus_di = 100 * (minus_dm.ewm(alpha=1 / self.adx_period, adjust=False).mean() / (tr_smooth + 1e-9))
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
        df['adx'] = dx.ewm(alpha=1 / self.adx_period, adjust=False).mean()

        # Keltner Channels
        df['kc_upper'] = df['ema_20'] + (df['atr'] * KELTNER_ATR_MULTIPLIER)
        df['kc_lower'] = df['ema_20'] - (df['atr'] * KELTNER_ATR_MULTIPLIER)
        df['kc_width'] = df['kc_upper'] - df['kc_lower']

        # SuperTrend
        hl2 = (high + low) / 2
        basic_upper = hl2 + (self.atr_multiplier * df['atr'])
        basic_lower = hl2 - (self.atr_multiplier * df['atr'])

        final_upper = pd.Series(index=df.index, dtype=float)
        final_lower = pd.Series(index=df.index, dtype=float)
        supertrend = pd.Series(index=df.index, dtype=float)
        direction = pd.Series(index=df.index, dtype=int)

        # 遞迴起點必須是 ATR 第一個有效值的那根，不能是第 0 根：ATR 是
        # atr_period 期滾動平均，前面幾根一定是 NaN，basic_upper/lower 算出來
        # 也是 NaN。如果從第 0 根開始遞迴，下面的棘輪邏輯全部是「跟前一根
        # 比大小」，只要比較對象是 NaN，Python/pandas 的比較結果永遠是
        # False，會一路落入 else 分支維持前一根的 NaN，一路傳染到最後一根，
        # 導致 final_upper/final_lower 永遠是 NaN、方向永遠卡在初始值 1
        # （多頭），不管實際價格怎麼走都不會翻轉——這是先前空單訊號被完全
        # 堵死、新鮮度分數永遠拿不到的根本原因。
        first_valid = df['atr'].first_valid_index()
        start_pos = df.index.get_loc(first_valid) if first_valid is not None else len(df)

        for i in range(len(df)):
            if i < start_pos:
                direction.iloc[i] = 1
                continue
            if i == start_pos:
                final_upper.iloc[i] = basic_upper.iloc[i]
                final_lower.iloc[i] = basic_lower.iloc[i]
                direction.iloc[i] = 1
                supertrend.iloc[i] = final_lower.iloc[i]
                continue

            if basic_upper.iloc[i] < final_upper.iloc[i-1] or close.iloc[i-1] > final_upper.iloc[i-1]:
                final_upper.iloc[i] = basic_upper.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i-1]

            if basic_lower.iloc[i] > final_lower.iloc[i-1] or close.iloc[i-1] < final_lower.iloc[i-1]:
                final_lower.iloc[i] = basic_lower.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i-1]

            prev_dir = direction.iloc[i-1]
            if prev_dir == 1:
                if close.iloc[i] < final_lower.iloc[i]:
                    direction.iloc[i] = -1
                    supertrend.iloc[i] = final_upper.iloc[i]
                else:
                    direction.iloc[i] = 1
                    supertrend.iloc[i] = final_lower.iloc[i]
            else:
                if close.iloc[i] > final_upper.iloc[i]:
                    direction.iloc[i] = 1
                    supertrend.iloc[i] = final_lower.iloc[i]
                else:
                    direction.iloc[i] = -1
                    supertrend.iloc[i] = final_upper.iloc[i]

        df['supertrend'] = supertrend
        df['st_direction'] = direction
        return df

    def evaluate_structured_entry(
        self, df: pd.DataFrame, ema_50_1h: float = None,
        st_direction_1h: int = None, btc_st_direction_1h: int = 0,
        symbol: str = None, indicators_precomputed: bool = False,
        is_dca_check: bool = False,
    ) -> dict:
        """Three closed-bar entries without MA7: breakout, support pullback, momentum cross."""
        if len(df) < max(65, STRUCTURED_SWING_LOOKBACK + 2):
            return {"action": "HOLD", "reason": "5m K線資料不足"}
        if not indicators_precomputed:
            df = self.compute_indicators(df)
        curr, prev = df.iloc[-1], df.iloc[-2]
        direction = int(curr["st_direction"])
        side = "LONG" if direction == 1 else "SHORT"
        price = float(curr["close"])
        atr = float(curr["atr"]) if not pd.isna(curr["atr"]) else price * 0.015
        volume = float(curr["volume"])
        volume_ma = float(curr["vol_ma_20"]) if not pd.isna(curr["vol_ma_20"]) else 0.0
        volume_ratio = volume / volume_ma if volume_ma > 0 else 0.0
        aligned = (
            (st_direction_1h in (None, direction))
            and (not btc_st_direction_1h or int(btc_st_direction_1h) == direction)
        )
        common = {
            "side": side, "price": price, "atr": atr,
            "signal_candle_low": float(curr["low"]),
            "signal_candle_high": float(curr["high"]),
            "volume_ratio": volume_ratio,
        }

        # 1h EMA50 大週期趨勢過濾：開倉方向必須與 1h EMA50 大趨勢同向
        if ENABLE_1H_EMA50_FILTER and not is_dca_check and ema_50_1h is not None and ema_50_1h > 0:
            if side == "LONG" and price < ema_50_1h:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "reason": f"價格低於 1h EMA50，逆勢做多拒絕開倉（價格 {price:.6g} < EMA50 {ema_50_1h:.6g}）", **common
                }
            elif side == "SHORT" and price > ema_50_1h:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "reason": f"價格高於 1h EMA50，逆勢做空拒絕開倉（價格 {price:.6g} > EMA50 {ema_50_1h:.6g}）", **common
                }

        # 計算支撐與壓力區（基於最近 24 根已收盤 of 5m K棒）
        if not is_dca_check and symbol is not None and 'low' in df.columns and 'high' in df.columns and len(df) >= 25:
            past_24_bars = df.iloc[-25:-1]
            support_level = float(past_24_bars['low'].min())
            resistance_level = float(past_24_bars['high'].max())

            # 做多：必須在支撐位 3% 內
            if side == "LONG" and price > support_level * 1.03:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "reason": f"價格不在支撐區3%內（當前 {price:.6g} > 支撐 {support_level:.6g}*1.03）", **common
                }

            # 做空：必須在壓力位 3% 內
            if side == "SHORT" and price < resistance_level * 0.97:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "reason": f"價格不在壓力區3%內（當前 {price:.6g} < 壓力 {resistance_level:.6g}*0.97）", **common
                }

        swing = df.iloc[-(STRUCTURED_SWING_LOOKBACK + 1):-1]
        prior_high = float(swing["high"].max())
        prior_low = float(swing["low"].min())
        kc_break = (
            price > float(curr["kc_upper"]) if side == "LONG"
            else price < float(curr["kc_lower"])
        )
        structure_break = price > prior_high if side == "LONG" else price < prior_low
        if ENABLE_BREAKOUT_ENTRY and aligned and (kc_break or structure_break) and volume_ratio >= STRUCTURED_VOLUME_MIN_RATIO:
            trigger = "KC上軌" if side == "LONG" and kc_break else "KC下軌" if side == "SHORT" and kc_break else "前高" if side == "LONG" else "前低"
            
            # ✅ 新增副策略：如果突破時成交量暴增 >= 2.0 倍均量，判定為主力強勢掃貨，直接以市價單進場搶籌
            if volume_ratio >= 2.0:
                return {
                    "action": "ENTER_MARKET", "entry_mode": "BREAKOUT", "score": 95,
                    "target_price": price,
                    "reason": f"Breakout_{side}｜🚀 [爆量強突破市價] 突破{trigger}｜量能{volume_ratio:.2f}x",
                    "prior_high": prior_high, "prior_low": prior_low, **common,
                }
            
            # 普通突破：維持原限價單等待回踩
            ema20 = float(curr["ema_20"])
            from core.config import BREAKOUT_PULLBACK_ATR_MULT
            if side == "LONG":
                pullback_target = ema20 + BREAKOUT_PULLBACK_ATR_MULT * atr
                pullback_target = min(pullback_target, price * 0.9995)
            else:
                pullback_target = ema20 - BREAKOUT_PULLBACK_ATR_MULT * atr
                pullback_target = max(pullback_target, price * 1.0005)
            return {
                "action": "ENTER_LIMIT", "entry_mode": "BREAKOUT", "score": 90,
                "target_price": pullback_target,
                "reason": f"Breakout_{side}｜突破{trigger}｜量能{volume_ratio:.2f}x｜等回踩@{pullback_target:.6g}",
                "prior_high": prior_high, "prior_low": prior_low, **common,
            }

        ema20 = float(curr["ema_20"])
        ema60 = float(df["close"].ewm(span=60, adjust=False).mean().iloc[-1])
        supports = [("5m EMA20", ema20), ("5m EMA60", ema60)]
        if ema_50_1h is not None:
            supports.append(("1h EMA50", float(ema_50_1h)))
        support_name, support_price = min(supports, key=lambda item: abs(price - item[1]))
        near_support = abs(price - support_price) <= atr * STRUCTURED_SUPPORT_NEAR_ATR
        body = abs(float(curr["close"]) - float(curr["open"]))
        lower_wick = min(float(curr["open"]), float(curr["close"])) - float(curr["low"])
        upper_wick = float(curr["high"]) - max(float(curr["open"]), float(curr["close"]))
        reversal = (
            float(curr["close"]) > float(curr["open"]) or lower_wick >= max(body, atr * 0.1)
            if side == "LONG"
            else float(curr["close"]) < float(curr["open"]) or upper_wick >= max(body, atr * 0.1)
        )
        volume_contracting = volume_ma > 0 and volume <= volume_ma
        rsi = float(curr["rsi"])
        rsi_ok = rsi >= 48.0 if side == "LONG" else rsi <= 52.0
        
        if aligned and near_support and reversal and volume_contracting and rsi_ok:
            return {
                "action": "ENTER_LIMIT", "entry_mode": "SUPPORT_PULLBACK", "score": 82,
                "target_price": support_price,
                "reason": f"SupportPullback_{side}｜{support_name}止跌｜縮量{volume_ratio:.2f}x｜RSI={rsi:.1f}",
                **common,
            }

        macd_hist_cross = (
            float(prev["macd_hist"]) <= 0 < float(curr["macd_hist"])
            if side == "LONG" else float(prev["macd_hist"]) >= 0 > float(curr["macd_hist"])
        )
        macd_line_cross = (
            float(prev["macd_line"]) <= float(prev["macd_signal"])
            and float(curr["macd_line"]) > float(curr["macd_signal"])
            and float(curr["macd_line"]) >= 0
            if side == "LONG" else
            float(prev["macd_line"]) >= float(prev["macd_signal"])
            and float(curr["macd_line"]) < float(curr["macd_signal"])
            and float(curr["macd_line"]) <= 0
        )
        rsi_cross = (
            float(prev["rsi"]) < 50 and float(curr["rsi"]) >= STRUCTURED_RSI_LONG_TRIGGER
            if side == "LONG" else
            float(prev["rsi"]) > 50 and float(curr["rsi"]) <= STRUCTURED_RSI_SHORT_TRIGGER
        )
        if ENABLE_MOMENTUM_CROSS_ENTRY and aligned and (macd_hist_cross or macd_line_cross or rsi_cross):
            triggers = []
            if macd_hist_cross or macd_line_cross:
                triggers.append("MACD交叉")
            if rsi_cross:
                triggers.append("RSI穿越50")
            return {
                "action": "ENTER_MARKET", "entry_mode": "MOMENTUM_CROSS", "score": 80,
                "reason": f"MomentumCross_{side}｜{'+'.join(triggers)}", **common,
            }

        return {
            "action": "HOLD", "side": side, "score": 0,
            "reason": "等待KC/前高突破、支撐止跌或MACD/RSI交叉", **common,
        }

    def evaluate_signal(
        self, df: pd.DataFrame,
        ema_50_1h: float = None,
        trend_1h_declining: bool = False,
        st_direction_1h: int = None,
        btc_st_direction_1h: int = 0,
        btc_st_flip_age: int = 999,
        symbol: str = None,
        parameter_overrides: dict = None,
        indicators_precomputed: bool = False,
    ) -> dict:
        if len(df) < 50:
            return {
                "action": "HOLD", "reason": "Not enough data",
                "eligible": False, "score_stage": "ELIGIBILITY",
            }

        if not indicators_precomputed:
            df = self.compute_indicators(df)
        curr = df.iloc[-1]
        overrides = dict(parameter_overrides or {})
        volume_min_ratio = float(overrides.get("volume_min_ratio", KELTNER_MIN_VOLUME_RATIO))
        atr_min_pct = float(overrides.get("atr_min_pct", MIN_ATR_PCT))
        rsi_long_max = float(overrides.get("rsi_long_max", RSI_LONG_MAX))
        rsi_short_min = float(overrides.get("rsi_short_min", RSI_SHORT_MIN))
        btc_score_penalty = overrides.get("btc_score_penalty")

        # --- 基本數據提取 ---
        price = curr['close_price_spike_filtered'] if ('close_price_spike_filtered' in curr and not pd.isna(curr['close_price_spike_filtered'])) else curr['close']
        atr = curr['atr'] if not np.isnan(curr['atr']) else price * 0.015
        rsi = curr['rsi']
        adx = curr['adx'] if not np.isnan(curr['adx']) else 0.0
        vol = curr['volume']
        vol_ma_20 = curr['vol_ma_20'] if not np.isnan(curr['vol_ma_20']) else 0
        kc_upper = curr['kc_upper']
        kc_lower = curr['kc_lower']
        kc_width = curr['kc_width'] if not np.isnan(curr['kc_width']) else (price * 0.03)
        ema_20 = curr['ema_20'] if not np.isnan(curr['ema_20']) else price
        ema_50 = curr['ema_50'] if not np.isnan(curr['ema_50']) else price

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
        if symbol is not None and 'low' in df.columns and 'high' in df.columns and len(df) >= 25:
            past_24_bars = df.iloc[-25:-1]
            support_level = float(past_24_bars['low'].min())
            resistance_level = float(past_24_bars['high'].max())

            # 做多：必須在支撐位 3% 內
            if st_dir == 1 and price > support_level * 1.03:
                return eligibility_hold(f"Mandatory_Fail: Price_Not_Near_Support({price:.6g}>{support_level:.6g}*1.03)")

            # 做空：必須在壓力位 3% 內
            if st_dir == -1 and price < resistance_level * 0.97:
                return eligibility_hold(f"Mandatory_Fail: Price_Not_Near_Resistance({price:.6g}<{resistance_level:.6g}*0.97)")

        # 層 A：BTC 大盤風險調整。剛翻轉仍暫停；方向相反改為扣分與縮倉，
        # 讓真正相對強勢的個幣仍可在通過其餘品質與回踩確認後進場。
        btc_regime = classify_btc_regime(
            st_dir, btc_st_direction_1h, btc_st_flip_age, symbol=symbol,
            score_penalty=btc_score_penalty,
        )
        if btc_regime["hard_block"]:
            block_reason = (
                "BTC_1h_ST_Contrary"
                if btc_regime["mode"] == "CONTRARY"
                else f"BTC_1h_ST_JustFlipped({btc_st_flip_age}bars<{BTC_REGIME_FLIP_BUFFER_BARS})"
            )
            return eligibility_hold(f"Mandatory_Fail: {block_reason}")

        # 層 B：個幣自身 1h SuperTrend 方向對齊
        # 1h SuperTrend 翻轉需要較長時間，比 price vs EMA50 準確 3~5 倍。
        # 1h SuperTrend 方向檢查已禁用，允許逆勢進場

        # 層 C：1h EMA50 輔助確認（第三道防線）
        # 1h SuperTrend 覆蓋不到的邊緣情況（如剛翻轉尚未展開），
        # 這一層確保價格需明顯站穩 EMA50 同側才允許。
        # 1h EMA50 方向檢查已禁用，允許逆勢進場

        # 防線 3：ADX 硬性最低門檻 — ADX 低於此值代表市場完全無趨勢動能，
        # 盤整期假突破發生率最高，直接 HOLD 不進入評分系統。
        # 注意：ADX 衰退擋單（D2，見下方）是另一種機制，兩者互補不衝突：
        # 這裡擋「太低」，D2 擋「方向沒變但動能在退潮」。
        if adx < ADX_MANDATORY_MIN:
            return eligibility_hold(f"Mandatory_Fail: ADX_Too_Low({adx:.1f}<{ADX_MANDATORY_MIN})")

        # 波動率過濾：ATR 佔價格比例太高或太低都不開倉。
        # 太高：SL/TP 用 ATR 倍數算出來的停損距離會被放大，同樣倉位金額下
        # 觸發止損時虧的錢遠大於移動止利能鎖住的獲利，是最大幾筆虧損的
        # 共同特徵。太低：市場太安靜時的「突破」更可能是盤整區間的假突破，
        # 沒有真實動能支撐，容易一進場就反轉（實測一批止損反推 ATR 只有
        # 0.07%~0.21%）。兩者一起框出一個波動適中的可交易區間。
        atr_pct = atr / price if price > 0 else 0
        if atr_pct > MAX_ATR_PCT:
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
        kc_breakout_buffer = kc_width * KELTNER_BREAKOUT_MARGIN_PCT
        # 防線 1：KC 突破收盤確認 — 取倒數第 2、3 根「已收盤」K 棒（iloc[-3:-1]）
        # 檢查收盤價是否仍在 KC 通道外，最新那根 iloc[-1] 可能尚未收盤不計入。
        # 這樣可過濾「影線剛碰到通道邊界但K棒尚未收出確認」的假突破訊號。
        past_slice = df.iloc[-3:-1]  # 前兩根已收盤K棒
        if len(past_slice) >= 1:
            if st_dir == 1:
                closed_confirmed = int((past_slice['close'] >= (past_slice['kc_upper'] + kc_breakout_buffer)).sum()) >= BREAKOUT_CONFIRM_BARS
            else:
                closed_confirmed = int((past_slice['close'] <= (past_slice['kc_lower'] - kc_breakout_buffer)).sum()) >= BREAKOUT_CONFIRM_BARS
        else:
            closed_confirmed = False  # 資料不足，保守處理視為未確認

        kc_breakout_passed = False
        if st_dir == 1 and price >= (kc_upper + kc_breakout_buffer) and closed_confirmed:
            score += 30
            kc_breakout_passed = True
            score_details.append("KC_Breakout_Pass")
        elif st_dir == -1 and price <= (kc_lower - kc_breakout_buffer) and closed_confirmed:
            score += 30
            kc_breakout_passed = True
            score_details.append("KC_Breakout_Pass")
        elif not closed_confirmed:
            score_details.append(f"KC_Breakout_NoClose({BREAKOUT_CONFIRM_BARS}bar_required)")
        else:
            score_details.append("KC_Breakout_Fail")

        # B. 量能確認分數 (20分)
        if vol_ma_20 > 0 and vol >= (vol_ma_20 * volume_min_ratio):
            score += 20
            score_details.append("Volume_Pass")
        else:
            score_details.append("Volume_Fail")

        # C. RSI 強勢分數 (20分)
        if st_dir == 1 and rsi >= RSI_LONG_THRESHOLD:
            score += 20
            score_details.append("RSI_Pass")
        elif st_dir == -1 and rsi <= RSI_SHORT_THRESHOLD:
            score += 20
            score_details.append("RSI_Pass")
        else:
            score_details.append("RSI_Fail")

        # D. 訊號新鮮度分數：初始突破只占 18/100，避免「方向尚未翻轉」
        # 跟真正 KC 突破同為 30 分，把老趨勢灌成不具預測力的 91+ 高分。
        # freshness_health_score 保留原本 30 分尺度，僅供老化硬門檻使用。
        st_flip_age = bars_since_supertrend_flip(df['st_direction'])
        freshness_ratio = max(0.0, 1.0 - st_flip_age / FRESHNESS_DECAY_BARS) if FRESHNESS_DECAY_BARS > 0 else 0.0
        freshness_health_score = round(freshness_ratio * 30)
        freshness_score = round(freshness_ratio * ENTRY_FRESHNESS_SCORE_MAX)
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
        adx_lookback_idx = len(df) - 1 - ADX_DECLINE_LOOKBACK_BARS
        adx_prior = df['adx'].iloc[adx_lookback_idx] if adx_lookback_idx >= 0 else np.nan
        adx_drop = (adx_prior - adx) if not pd.isna(adx_prior) else 0.0
        adx_declining = (
            not pd.isna(adx_prior)
            and adx_drop >= max(ADX_DECLINE_MIN_DROP, adx_prior * ADX_DECLINE_MIN_DROP_RATIO)
        )
        adx_declining_exhausted = adx_declining and adx < WEAK_ENERGY_ADX_THRESHOLD

        # D3. 價格乖離檢查：價格距離 EMA20 太遠（用 ATR 正規化衡量），代表
        # 這波已經漲/跌很多才追進場，均值回歸風險高，容易一進場就被拉回。
        # 跟 KC 突破（E4/A）是兩件事——KC 突破只要求價格超出通道邊界一點，
        # 這裡抓的是「超出太多」的極端情況。
        ema20_distance_atr = abs(price - ema_20) / atr if atr > 0 else 0.0
        price_overextended = ema20_distance_atr > EMA_EXTENSION_MAX_ATR_MULT

        # E. 品質細分加分（0~12分）：讓同樣達標 70/80 分的訊號能再分出優劣，
        # 用於同一輪多個候選訊號時挑選最優的下單，而不是隨機/先到先進場。
        # 四項各佔 0~3 分，數值越好加分越多，實測跟虧損大小/勝率相關：
        quality_bonus = 0
        # E1. 波動品質：越接近 [MIN_ATR_PCT, MAX_ATR_PCT] 區間的中點分數越高，
        # 越靠近任一邊界（太安靜或太劇烈）分數越低。不能只獎勵「越低越好」，
        # 因為太低的 ATR 反而是假突破風險（見上面的 Mandatory_Fail 說明）。
        atr_mid = (atr_min_pct + MAX_ATR_PCT) / 2.0
        atr_half_range = (MAX_ATR_PCT - atr_min_pct) / 2.0
        atr_quality = (
            max(0.0, 1.0 - abs(atr_pct - atr_mid) / atr_half_range)
            if atr_half_range > 0 else 0.0
        )
        quality_bonus += round(atr_quality * 3)
        # E2. RSI 品質：獎勵健康動能區中段，不再讓越極端的 RSI 得分越高。
        rsi_ideal = 60.0 if st_dir == 1 else 40.0
        rsi_half_width = max(
            rsi_ideal - RSI_LONG_THRESHOLD if st_dir == 1 else RSI_SHORT_THRESHOLD - rsi_ideal,
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
        adx_ratio = (adx - ADX_QUALITY_MIN) / (ADX_QUALITY_FULL - ADX_QUALITY_MIN)
        quality_bonus += round(min(max(adx_ratio, 0.0), 1.0) * 3)
        # ADX 仍高於品質底線時，下降只代表趨勢強度轉弱，不能直接取消；
        # 輕扣 1 分品質，保留高分訊號進入回踩確認。低於底線才由硬性規則攔截。
        adx_decline_soft_penalty = 1 if adx_declining and not adx_declining_exhausted else 0
        if adx_decline_soft_penalty:
            quality_bonus = max(0, quality_bonus - adx_decline_soft_penalty)
            score_details.append(
                f"ADX_Declining_Soft-1({adx:.1f}<{adx_prior:.1f};floor={WEAK_ENERGY_ADX_THRESHOLD:.1f})"
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
        if score >= MIN_SCORE_THRESHOLD and not kc_breakout_passed:
            return scored_hold(
                f"Mandatory_Fail: KC_Breakout_Unconfirmed | Score({score}) | {', '.join(score_details)}"
            )

        if score >= MIN_SCORE_THRESHOLD and quality_bonus < ENTRY_MIN_QUALITY_BONUS:
            return scored_hold(
                f"Mandatory_Fail: Entry_Quality_Too_Low"
                f"({quality_bonus}<{ENTRY_MIN_QUALITY_BONUS}) | Score({score}) | "
                f"{', '.join(score_details)}"
            )

        if score >= MIN_SCORE_THRESHOLD and freshness_health_score < MIN_FRESHNESS_SCORE:
            return scored_hold(
                f"Mandatory_Fail: Freshness_Too_Stale({st_flip_age}bars) | Score({score}) | {', '.join(score_details)}"
            )

        # 額外防線：只有 ADX 已跌破品質底線且持續衰退才硬擋。ADX 仍在
        # 品質底線以上時已於品質分輕扣 1 分，不取消仍具強度的趨勢訊號。
        if score >= MIN_SCORE_THRESHOLD and adx_declining_exhausted:
            return scored_hold(
                f"Mandatory_Fail: ADX_Declining_Exhaustion({adx:.1f}<{adx_prior:.1f}) | Score({score}) | {', '.join(score_details)}"
            )

        # 額外防線：價格已經乖離 EMA20 太遠（見上面 D3），代表這波已經漲/
        # 跌很多才追進場，均值回歸風險高，不管總分靠其他項目湊得多高。
        if score >= MIN_SCORE_THRESHOLD and price_overextended:
            return scored_hold(
                f"Mandatory_Fail: Price_Overextended({ema20_distance_atr:.1f}x_ATR) | Score({score}) | {', '.join(score_details)}"
            )

        # 額外防線：大週期（1h）本身的動能也在衰退（見 engine.py
        # update_1h_trend_cache 用同一批1h K線算的 ADX 衰退判斷），代表
        # 不只是5分K的小趨勢要提防，連大方向本身都已經在做頭/做底，
        # 這是5分K的新鮮度/ADX檢查看不到的更高層級末端訊號。
        if MIN_SCORE_THRESHOLD <= score < 90 and trend_1h_declining:
            return scored_hold(
                f"Mandatory_Fail: 1h_Trend_Declining | Score({score}) | {', '.join(score_details)}"
            )
        if score >= 90 and trend_1h_declining:
            # 90+ 現價 Maker 試行：高完整度突破不再被 1h ADX 衰退單獨否決。
            # 方向、ATR、RSI、KC、品質等前置資格仍已全部通過。
            score_details.append("1h_Trend_Declining_90Plus_Allowed")

        if score >= MIN_SCORE_THRESHOLD:
            pullback_depth = get_pullback_target_depth(score)
            kc_edge = kc_upper if st_dir == 1 else kc_lower
            side = "LONG" if st_dir == 1 else "SHORT"
            current_maker = score >= 90
            if current_maker:
                pullback_target = float(price)
                pullback_distance = 0.0
            else:
                pullback_target, pullback_distance, pullback_room_ok = compute_pullback_target(
                    kc_edge, ema_20, atr, side, score
                )
                if not pullback_room_ok:
                    return scored_hold(
                        f"Mandatory_Fail: Pullback_Range_Too_Narrow"
                        f"({pullback_distance / max(atr, 1e-12):.2f}ATR<"
                        f"{PULLBACK_TARGET_MIN_ATR_MULT:.2f}ATR) | Score({score}) | "
                        f"{', '.join(score_details)}"
                    )
            confirmation_reason = (
                f"ADX {adx:.1f}←{adx_prior:.1f}仍高於{WEAK_ENERGY_ADX_THRESHOLD:g}，品質-1，等待回調二次確認"
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
                dist = (kc_lower - price) / kc_lower
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

    def confirm_pullback_entry(
        self, df: pd.DataFrame, side: str, ema_1h: float = None, trend_1h_declining: bool = False,
        btc_st_direction_1h: int = 0, btc_st_flip_age: int = 999, symbol: str = None,
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

        df = self.compute_indicators(df)
        curr = df.iloc[-1]
        price = curr['close_price_spike_filtered'] if ('close_price_spike_filtered' in curr and not pd.isna(curr['close_price_spike_filtered'])) else curr['close']
        atr = curr['atr'] if not np.isnan(curr['atr']) else price * 0.015
        rsi = curr['rsi']
        vol = curr['volume']
        vol_ma_20 = curr['vol_ma_20'] if not np.isnan(curr['vol_ma_20']) else 0
        ema_20 = curr['ema_20'] if not pd.isna(curr['ema_20']) else price
        st_dir = curr['st_direction']
        want_dir = 1 if side == "LONG" else -1

        if st_dir != want_dir:
            return {
                "status": "CANCEL",
                "reason": f"SuperTrend 方向已反轉（現為{'多頭' if st_dir == 1 else '空頭'}）",
            }

        btc_regime = classify_btc_regime(
            want_dir, btc_st_direction_1h, btc_st_flip_age, symbol=symbol
        )
        if btc_regime["hard_block"]:
            reason = (
                "BTC 1h 方向背離，禁止逆大盤進場"
                if btc_regime["mode"] == "CONTRARY"
                else f"BTC 1h 剛翻轉，仍在 {btc_st_flip_age}/{BTC_REGIME_FLIP_BUFFER_BARS} 根緩衝期"
            )
            return {"status": "CANCEL", "reason": reason}

        if side == "LONG" and rsi > RSI_LONG_MAX:
            return {"status": "CANCEL", "reason": f"RSI過熱 RSI_Overbought({rsi:.1f}>{RSI_LONG_MAX:.1f})"}
        if side == "SHORT" and rsi < RSI_SHORT_MIN:
            return {"status": "CANCEL", "reason": f"RSI過冷 RSI_Oversold({rsi:.1f}<{RSI_SHORT_MIN:.1f})"}

        # 大週期（1h）本身動能衰退時，即使 5 分K條件仍成立也取消。
        if trend_1h_declining:
            return {
                "status": "CANCEL",
                "reason": "大週期(1h)動能已在衰退 1h_Trend_Declining",
            }

        # 價格乖離 EMA20 太遠：等回踩的這段時間裡價格可能又衝更遠，均值
        # 回歸風險比登記當下更高，一樣取消。
        ema20_distance_atr = abs(price - ema_20) / atr if atr > 0 else 0.0
        if ema20_distance_atr > EMA_EXTENSION_MAX_ATR_MULT:
            return {
                "status": "CANCEL",
                "reason": f"價格乖離EMA20過大 Price_Overextended({ema20_distance_atr:.1f}x_ATR)",
            }

        # 回踩跌破/突破 EMA20：健康的回調應該只是往 EMA20 靠近，不會真的
        # 穿越到對面——多單回踩時價格已經跌破 EMA20（或空單回踩時已經站
        # 上 EMA20），代表這已經不是「回調」，而是價格真的穿越均線在反轉。
        # 跟上面「乖離過大」是兩個不同方向的風險：那個抓「離 EMA20 太遠」
        # （不管在哪一側），這個抓「跑到 EMA20 錯的那一側」，兩種情況都
        # 可能發生、必須分開判斷，缺一不可。
        if side == "LONG" and price < ema_20:
            return {
                "status": "CANCEL",
                "reason": f"回踩跌破EMA20，疑似真反轉 EMA20_Breached(price={price:.6f}<ema20={ema_20:.6f})",
            }
        if side == "SHORT" and price > ema_20:
            return {
                "status": "CANCEL",
                "reason": f"回踩突破EMA20，疑似真反轉 EMA20_Breached(price={price:.6f}>ema20={ema_20:.6f})",
            }

        # 距離原始突破過了多久：方向沒反轉不代表這個突破還「新鮮」——等回踩
        # 的這段時間裡，行情可能只是在原地震盪消耗動能，SuperTrend 遲遲沒
        # 真的翻轉，但這個突破本身已經是強弩之末。跟 evaluate_signal() 的
        # 新鮮度用同一套連續淡化公式重新算一次，太舊直接取消，不是只看
        # 方向對不對。
        st_flip_age = bars_since_supertrend_flip(df['st_direction'])
        freshness_ratio = max(0.0, 1.0 - st_flip_age / FRESHNESS_DECAY_BARS) if FRESHNESS_DECAY_BARS > 0 else 0.0
        freshness_score = round(freshness_ratio * 30)
        if freshness_score < MIN_FRESHNESS_SCORE:
            return {
                "status": "CANCEL",
                "reason": f"距離原始突破已過太久 Freshness({st_flip_age}bars)={freshness_score}<{MIN_FRESHNESS_SCORE}",
            }

        # ADX 動能衰退檢查（跟 evaluate_signal() 同一套邏輯）：方向沒反轉、
        # 新鮮度也還夠，但 ADX 現在比 N 根K棒前低且已經低於
        # WEAK_ENERGY_ADX_THRESHOLD，代表動能在等待回踩的這段時間持續
        # 衰退，一樣是末端趨勢的樣貌。
        adx = curr['adx'] if not pd.isna(curr['adx']) else 0.0
        adx_lookback_idx = len(df) - 1 - ADX_DECLINE_LOOKBACK_BARS
        adx_prior = df['adx'].iloc[adx_lookback_idx] if adx_lookback_idx >= 0 else np.nan
        adx_drop = (adx_prior - adx) if not pd.isna(adx_prior) else 0.0
        adx_declining = (
            not pd.isna(adx_prior)
            and adx_drop >= max(ADX_DECLINE_MIN_DROP, adx_prior * ADX_DECLINE_MIN_DROP_RATIO)
        )
        adx_declining_exhausted = adx_declining and adx < WEAK_ENERGY_ADX_THRESHOLD
        if adx_declining_exhausted:
            return {
                "status": "CANCEL",
                "reason": (
                    f"ADX 動能持續衰退 {adx:.1f}<{adx_prior:.1f}"
                    f"（{ADX_DECLINE_LOOKBACK_BARS}根K棒前）且低於能量門檻 {WEAK_ENERGY_ADX_THRESHOLD:.1f}"
                ),
            }

        if ema_1h is not None:
            if side == "LONG" and price < ema_1h:
                return {"status": "CANCEL", "reason": "1h 大趨勢已轉空"}
            if side == "SHORT" and price > ema_1h:
                return {"status": "CANCEL", "reason": "1h 大趨勢已轉多"}

        atr_pct = atr / price if price > 0 else 0
        if atr_pct > MAX_ATR_PCT:
            return {"status": "CANCEL", "reason": f"波動率轉為過高 ATR_Too_High({atr_pct:.2%})"}
        if atr_pct < MIN_ATR_PCT:
            return {"status": "CANCEL", "reason": f"波動率轉為過低 ATR_Too_Low({atr_pct:.2%})"}

        # 回踩縮量使用多空合計成交量，無法辨識是順勢量或逆勢量；尤其
        # 回踩時縮量常屬正常型態，因此只讓量能項記 0 分，不再硬取消。
        volume_faded = False
        recent_vol_avg = None
        min_sustain_vol = None
        if vol_ma_20 > 0 and len(df) >= 3:
            recent_vol_avg = float(df['volume'].iloc[-3:-1].mean())
            min_sustain_vol = float(vol_ma_20 * POST_BREAKOUT_VOL_SUSTAIN_RATIO)
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
            and abs(candle_close - candle_open) >= atr * ADVERSE_PULLBACK_BODY_MIN_ATR_MULT
            and volume_ratio >= ADVERSE_PULLBACK_VOLUME_SPIKE_RATIO
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
            and vol >= vol_ma_20 * KELTNER_MIN_VOLUME_RATIO
            else 0
        )
        score_c = 20 if (
            (side == "LONG" and rsi >= RSI_LONG_THRESHOLD)
            or (side == "SHORT" and rsi <= RSI_SHORT_THRESHOLD)
        ) else 0
        score_d = freshness_score

        atr_mid = (MIN_ATR_PCT + MAX_ATR_PCT) / 2.0
        atr_half_range = (MAX_ATR_PCT - MIN_ATR_PCT) / 2.0
        atr_quality = (
            max(0.0, 1.0 - abs(atr_pct - atr_mid) / atr_half_range)
            if atr_half_range > 0 else 0.0
        )
        rsi_ideal = 60.0 if side == "LONG" else 40.0
        rsi_half_width = max(
            rsi_ideal - RSI_LONG_THRESHOLD if side == "LONG" else RSI_SHORT_THRESHOLD - rsi_ideal,
            1.0,
        )
        rsi_quality = max(0.0, 1.0 - abs(rsi - rsi_ideal) / rsi_half_width)
        vol_ratio = (vol / vol_ma_20) if vol_ma_20 > 0 else 0.0
        vol_margin = max(0.0, vol_ratio - KELTNER_MIN_VOLUME_RATIO)
        adx_ratio = (adx - ADX_QUALITY_MIN) / (ADX_QUALITY_FULL - ADX_QUALITY_MIN)
        score_e = (
            round(atr_quality * 3)
            + round(rsi_quality * 3)
            + round(min(vol_margin / 1.0, 1.0) * 3)
            + round(min(max(adx_ratio, 0.0), 1.0) * 3)
        )
        adx_decline_soft_penalty = 1 if adx_declining and not adx_declining_exhausted else 0
        if adx_decline_soft_penalty:
            score_e = max(0, score_e - adx_decline_soft_penalty)

        if score_e < ENTRY_MIN_QUALITY_BONUS:
            return {
                "status": "CANCEL",
                "reason": f"回調品質不足 Quality_Too_Low({score_e}<{ENTRY_MIN_QUALITY_BONUS})",
                "adverse_volume_spike": adverse_volume_spike,
                "adverse_volume_ratio": volume_ratio,
            }

        raw_pullback_score = score_b + score_c + score_d + score_e
        pullback_score = max(0, raw_pullback_score - btc_regime["score_penalty"])
        if pullback_score < PULLBACK_SCORE_THRESHOLD:
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
                    f"回調總分不足 Pullback_Score({pullback_score}<{PULLBACK_SCORE_THRESHOLD}) | "
                    f"Volume+{score_b} RSI+{score_c} Freshness+{score_d} Quality+{score_e} "
                    f"BTC-{btc_regime['score_penalty']}"
                ),
            }

        return {
            "status": "PASS",
            "reason": (
                f"二次確認通過 Pullback_Score({pullback_score})"
                + (
                    f" | ADX {adx:.1f}←{adx_prior:.1f}仍高於{WEAK_ENERGY_ADX_THRESHOLD:g}，品質-1"
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
