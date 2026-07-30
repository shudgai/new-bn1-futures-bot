import pandas as pd
import numpy as np
from core.config import (
    STOP_LOSS_MULTIPLIER, TAKE_PROFIT_MULTIPLIER, TAKER_FEE_RATE, MIN_NET_REWARD_RISK,
    KELTNER_BREAKOUT_MARGIN_PCT, KELTNER_MIN_VOLUME_RATIO, FRESHNESS_DECAY_BARS,
    ENTRY_FRESHNESS_SCORE_MAX,
    MIN_FRESHNESS_SCORE,
    RSI_LONG_THRESHOLD, RSI_SHORT_THRESHOLD, RSI_LONG_MAX, RSI_SHORT_MIN,
    MIN_SCORE_THRESHOLD, ENTRY_MIN_QUALITY_BONUS, PULLBACK_ZONE_PCT, MAX_ATR_PCT, MIN_ATR_PCT,
    KELTNER_ATR_MULTIPLIER, PULLBACK_TARGET_DEPTH, MIN_SL_DISTANCE_PCT,
    ADX_PERIOD, ADX_QUALITY_MIN, ADX_QUALITY_FULL, ADX_DECLINE_LOOKBACK_BARS,
    ADX_DECLINE_MIN_DROP, ADX_DECLINE_MIN_DROP_RATIO,
    EMA_EXTENSION_MAX_ATR_MULT, PULLBACK_SCORE_THRESHOLD, DISASTER_STOP_MULTIPLIER,
    ADX_MANDATORY_MIN, BREAKOUT_CONFIRM_BARS, POST_BREAKOUT_VOL_SUSTAIN_RATIO,
    TREND_AGREE_EMA_MARGIN_PCT,
    BTC_REGIME_FILTER_ENABLED, BTC_REGIME_FLIP_BUFFER_BARS, BTC_REGIME_SCORE_PENALTY,
    BTC_REGIME_ALLOCATION_FACTOR, SYMBOL_1H_ST_FILTER_ENABLED,
)
from core.indicators import bars_since_supertrend_flip

def classify_btc_regime(st_direction: int, btc_direction: int, flip_age: int, symbol: str = None) -> dict:
    """Return the BTC entry adjustment without blocking healthy relative-strength trades."""
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
        context.update(
            mode="CONTRARY",
            score_penalty=BTC_REGIME_SCORE_PENALTY,
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

class SuperTrendKeltnerStrategy:
    """
    高精度量化引擎 - 回調狙擊版本 (Pullback Sniper Mode)
    核心邏輯：
    1. 底線防禦 (Mandatory)：大週期趨勢 (1h EMA50) 與 SuperTrend 方向必須一致。
    2. 動態評分 (Scoring)：Keltner 突破、量能、RSI、訊號新鮮度 進行加權評分。
    3. 所有達標訊號一律 WAIT_PULLBACK，等待 50% 回踩與成交前二次確認。
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

        # 成交量均線
        df['vol_ma_20'] = volume.rolling(window=20).mean()

        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        df['rsi'] = 100 - (100 / (1 + rs))

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

    def evaluate_signal(
        self, df: pd.DataFrame,
        ema_50_1h: float = None,
        trend_1h_declining: bool = False,
        st_direction_1h: int = None,
        btc_st_direction_1h: int = 0,
        btc_st_flip_age: int = 999,
        symbol: str = None,
    ) -> dict:
        if len(df) < 50:
            return {
                "action": "HOLD", "reason": "Not enough data",
                "eligible": False, "score_stage": "ELIGIBILITY",
            }

        df = self.compute_indicators(df)
        curr = df.iloc[-1]

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

        def eligibility_hold(reason: str) -> dict:
            return {
                "action": "HOLD", "reason": reason,
                "eligible": False, "score_stage": "ELIGIBILITY",
            }

        # --- 1. 底線防禦 (Mandatory Filters) ---

        st_dir = curr['st_direction']

        # 層 A：BTC 大盤風險調整。剛翻轉仍暫停；方向相反改為扣分與縮倉，
        # 讓真正相對強勢的個幣仍可在通過其餘品質與回踩確認後進場。
        btc_regime = classify_btc_regime(
            st_dir, btc_st_direction_1h, btc_st_flip_age, symbol=symbol
        )
        if btc_regime["hard_block"]:
            return eligibility_hold(
                f"Mandatory_Fail: BTC_1h_ST_JustFlipped({btc_st_flip_age}bars<{BTC_REGIME_FLIP_BUFFER_BARS})"
            )

        # 層 B：個幣自身 1h SuperTrend 方向對齊
        # 1h SuperTrend 翻轉需要較長時間，比 price vs EMA50 準確 3~5 倍。
        # 要求 5m SuperTrend 方向 == 個幣 1h SuperTrend 方向才允許開倉。
        if SYMBOL_1H_ST_FILTER_ENABLED and st_direction_1h is not None:
            if st_dir == 1 and st_direction_1h == -1:
                return eligibility_hold("Mandatory_Fail: Symbol_1h_ST_Bearish(vs_5m_LONG)")
            if st_dir == -1 and st_direction_1h == 1:
                return eligibility_hold("Mandatory_Fail: Symbol_1h_ST_Bullish(vs_5m_SHORT)")

        # 層 C：1h EMA50 輔助確認（第三道防線）
        # 1h SuperTrend 覆蓋不到的邊緣情況（如剛翻轉尚未展開），
        # 這一層確保價格需明顯站穩 EMA50 同側才允許。
        ema_50_upper_band = ema_50_1h * (1 + TREND_AGREE_EMA_MARGIN_PCT) if ema_50_1h else None
        ema_50_lower_band = ema_50_1h * (1 - TREND_AGREE_EMA_MARGIN_PCT) if ema_50_1h else None
        is_1h_bullish = (price >= ema_50_upper_band) if ema_50_upper_band is not None else True
        is_1h_bearish = (price <= ema_50_lower_band) if ema_50_lower_band is not None else True
        if st_dir == 1 and not is_1h_bullish:
            return eligibility_hold("Mandatory_Fail: 1h_EMA50_Bearish")
        if st_dir == -1 and not is_1h_bearish:
            return eligibility_hold("Mandatory_Fail: 1h_EMA50_Bullish")

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
        if atr_pct < MIN_ATR_PCT:
            return eligibility_hold(f"Mandatory_Fail: ATR_Too_Low({atr_pct:.2%})")

        # 極端 RSI 代表行情已經過熱／過冷，不是更高品質的追價訊號。
        if st_dir == 1 and rsi > RSI_LONG_MAX:
            return eligibility_hold(f"Mandatory_Fail: RSI_Overbought({rsi:.1f}>{RSI_LONG_MAX:.1f})")
        if st_dir == -1 and rsi < RSI_SHORT_MIN:
            return eligibility_hold(f"Mandatory_Fail: RSI_Oversold({rsi:.1f}<{RSI_SHORT_MIN:.1f})")


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
        if vol_ma_20 > 0 and vol >= (vol_ma_20 * KELTNER_MIN_VOLUME_RATIO):
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
        # 且已經低於 ADX_QUALITY_MIN，代表這不是「本來就安靜」而是「正在
        # 退潮」，直接擋單。
        adx_lookback_idx = len(df) - 1 - ADX_DECLINE_LOOKBACK_BARS
        adx_prior = df['adx'].iloc[adx_lookback_idx] if adx_lookback_idx >= 0 else np.nan
        adx_drop = (adx_prior - adx) if not pd.isna(adx_prior) else 0.0
        adx_declining_exhausted = (
            not pd.isna(adx_prior)
            and adx_drop >= max(ADX_DECLINE_MIN_DROP, adx_prior * ADX_DECLINE_MIN_DROP_RATIO)
        )

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
        atr_mid = (MIN_ATR_PCT + MAX_ATR_PCT) / 2.0
        atr_half_range = (MAX_ATR_PCT - MIN_ATR_PCT) / 2.0
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
        vol_margin = max(0.0, vol_ratio - KELTNER_MIN_VOLUME_RATIO)
        quality_bonus += round(min(vol_margin / 1.0, 1.0) * 3)
        # E4. 趨勢強度（ADX）：ADX 越高代表越像真的有趨勢動能撐著，越低越像
        # 盤整期雜訊——KC 突破配上低 ADX，正是假突破最常見的樣貌之一。
        # ADX_QUALITY_MIN 以下不加分，ADX_QUALITY_FULL 以上視為滿分。
        adx_ratio = (adx - ADX_QUALITY_MIN) / (ADX_QUALITY_FULL - ADX_QUALITY_MIN)
        quality_bonus += round(min(max(adx_ratio, 0.0), 1.0) * 3)

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
        }

        def scored_hold(reason: str) -> dict:
            return {"action": "HOLD", "score": score, "reason": reason, **btc_context}

        # --- 3. 回調狙擊最終決策 (Pullback Sniper Mode) ---
        # 修正核心：KC 突破是「訊號觸發」，等價格回踩 KC 軌道後才是「進場時機」
        # 進場門檻：總分 >= MIN_SCORE_THRESHOLD（預設 65 分）
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

        # 額外防線：ADX 動能已經在衰退（見上面 D2）直接擋單，不管總分靠
        # 其他項目湊得多高——這種「方向沒變、新鮮度分數也不低，但 ADX
        # 連續下滑」正是新鮮度抓不到的另一種末端趨勢樣貌。
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
        if score >= MIN_SCORE_THRESHOLD and trend_1h_declining:
            return scored_hold(
                f"Mandatory_Fail: 1h_Trend_Declining | Score({score}) | {', '.join(score_details)}"
            )

        if score >= MIN_SCORE_THRESHOLD:
            # 不再因高分直接市價追突破；達標訊號全部等待 50% 回踩，成交前再
            # 用最新 K 線做二次確認，避免開在趨勢末端與均線背離的位置。
            downgrade_note = " | MarketChase_Disabled"
            if st_dir == 1:
                dist = (price - kc_upper) / kc_upper
                pullback_target = kc_upper - (kc_upper - ema_20) * PULLBACK_TARGET_DEPTH
                return {
                    "action": "WAIT_PULLBACK", "side": "LONG",
                    "price": price, "atr": atr,
                    "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                    "target_zone": pullback_target,
                    **btc_context,
                    "reason": f"Pullback_WAIT({score}) | dist={dist:.2%} | Target={pullback_target:.4f} | {', '.join(score_details)}{downgrade_note}"
                }
            else:  # SHORT
                dist = (kc_lower - price) / kc_lower
                pullback_target = kc_lower + (ema_20 - kc_lower) * PULLBACK_TARGET_DEPTH
                return {
                    "action": "WAIT_PULLBACK", "side": "SHORT",
                    "price": price, "atr": atr,
                    "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                    "target_zone": pullback_target,
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
            return {
                "status": "CANCEL",
                "reason": f"BTC 1h 剛翻轉，仍在 {btc_st_flip_age}/{BTC_REGIME_FLIP_BUFFER_BARS} 根緩衝期",
            }

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
        # 新鮮度也還夠，但 ADX 現在比 N 根K棒前低且已經低於 ADX_QUALITY_MIN，
        # 代表動能在等待回踩的這段時間持續衰退，一樣是末端趨勢的樣貌。
        adx = curr['adx'] if not pd.isna(curr['adx']) else 0.0
        adx_lookback_idx = len(df) - 1 - ADX_DECLINE_LOOKBACK_BARS
        adx_prior = df['adx'].iloc[adx_lookback_idx] if adx_lookback_idx >= 0 else np.nan
        adx_drop = (adx_prior - adx) if not pd.isna(adx_prior) else 0.0
        if (
            not pd.isna(adx_prior)
            and adx_drop >= max(ADX_DECLINE_MIN_DROP, adx_prior * ADX_DECLINE_MIN_DROP_RATIO)
        ):
            return {
                "status": "CANCEL",
                "reason": f"ADX 動能持續衰退 {adx:.1f}<{adx_prior:.1f}（{ADX_DECLINE_LOOKBACK_BARS}根K棒前）",
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

        # 防線 2：突破後量能持續性確認 — 假突破特徵之一是「突破當根量大，
        # 後續量立刻萎縮」，代表突破動能已耗盡，接下來的回踩是真正反轉。
        # 取倒數第 2、3 根 K 棒（iloc[-3:-1]）的平均量能；最新那根（iloc[-1]）
        # 當前 K 棒尚未收盤、量能仍在累積，不用於穩定性判斷。
        if vol_ma_20 > 0 and len(df) >= 3:
            recent_vol_avg = df['volume'].iloc[-3:-1].mean()
            min_sustain_vol = vol_ma_20 * POST_BREAKOUT_VOL_SUSTAIN_RATIO
            if recent_vol_avg < min_sustain_vol:
                return {
                    "status": "CANCEL",
                    "reason": (
                        f"突破後量能萎縮 Vol_Fade({recent_vol_avg:.0f}<{min_sustain_vol:.0f}=均量×{POST_BREAKOUT_VOL_SUSTAIN_RATIO})"
                    ),
                }

        # 回調總分（B量能+C RSI+D新鮮度+E品質加分，滿分79，跟 evaluate_signal()
        # 同一套加權）：量能/RSI 不再各自當硬性關卡、任一項不過就整筆取消，
        # 改成允許互相補償——量能爆量成長可以補足 RSI 差一點點的缺口，更貼近
        # 真實交易判斷。上面的方向反轉/大趨勢衰退/新鮮度太舊/ADX動能衰退/
        # 價格乖離過大/ATR%範圍 是絕對紅線，不受總分補償影響，維持硬性取消。
        score_b = 20 if (vol_ma_20 > 0 and vol >= vol_ma_20 * KELTNER_MIN_VOLUME_RATIO) else 0
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

        if score_e < ENTRY_MIN_QUALITY_BONUS:
            return {
                "status": "CANCEL",
                "reason": f"回調品質不足 Quality_Too_Low({score_e}<{ENTRY_MIN_QUALITY_BONUS})",
            }

        raw_pullback_score = score_b + score_c + score_d + score_e
        pullback_score = max(0, raw_pullback_score - btc_regime["score_penalty"])
        if pullback_score < PULLBACK_SCORE_THRESHOLD:
            return {
                "status": "CANCEL",
                "raw_pullback_score": raw_pullback_score,
                "pullback_score": pullback_score,
                "reason": (
                    f"回調總分不足 Pullback_Score({pullback_score}<{PULLBACK_SCORE_THRESHOLD}) | "
                    f"Volume+{score_b} RSI+{score_c} Freshness+{score_d} Quality+{score_e} "
                    f"BTC-{btc_regime['score_penalty']}"
                ),
            }

        return {
            "status": "PASS",
            "reason": f"二次確認通過 Pullback_Score({pullback_score})",
            "raw_pullback_score": raw_pullback_score,
            "pullback_score": pullback_score,
            "btc_regime_mode": btc_regime["mode"],
            "btc_direction_1h": int(btc_st_direction_1h or 0),
            "btc_score_penalty": btc_regime["score_penalty"],
            "btc_allocation_factor": btc_regime["allocation_factor"],
        }
