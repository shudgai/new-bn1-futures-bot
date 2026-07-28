import pandas as pd
import numpy as np
from core.config import (
    STOP_LOSS_MULTIPLIER, TAKE_PROFIT_MULTIPLIER,
    KELTNER_BREAKOUT_MARGIN_PCT, KELTNER_MIN_VOLUME_RATIO, FRESHNESS_DECAY_BARS,
    MIN_FRESHNESS_SCORE,
    RSI_LONG_THRESHOLD, RSI_SHORT_THRESHOLD,
    MIN_SCORE_THRESHOLD, PULLBACK_ZONE_PCT, MAX_ATR_PCT, MIN_ATR_PCT,
    KELTNER_ATR_MULTIPLIER, PULLBACK_TARGET_DEPTH, MIN_SL_DISTANCE_PCT,
    ADX_PERIOD, ADX_QUALITY_MIN, ADX_QUALITY_FULL,
)
from core.indicators import bars_since_supertrend_flip


def compute_sl_tp_distance(price: float, atr: float) -> tuple[float, float]:
    """算出止損/止盈距離，並套用 MIN_SL_DISTANCE_PCT 下限，避免低波動期間
    ATR 太小導致止損距離縮到容易被雜訊掃出的地步。回傳 (sl_distance, tp_distance)。"""
    sl_distance = max(atr * STOP_LOSS_MULTIPLIER, price * MIN_SL_DISTANCE_PCT)
    tp_distance = sl_distance * (TAKE_PROFIT_MULTIPLIER / STOP_LOSS_MULTIPLIER)
    return sl_distance, tp_distance

class SuperTrendKeltnerStrategy:
    """
    高精度量化引擎 - 回調狙擊版本 (Pullback Sniper Mode)
    核心邏輯：
    1. 底線防禦 (Mandatory)：大週期趨勢 (1h EMA50) 與 SuperTrend 方向必須一致。
    2. 動態評分 (Scoring)：Keltner 突破、量能、RSI、訊號新鮮度 進行加權評分。
    3. 進場決策「回調優先」三段式：
       - 評分 >= 90 且超出 KC 距離 <= 0.1% → BUY_NOW  (剛突破，立即開倉)
       - 評分 >= 90 且超出 KC 距離 > 0.1% → WAIT_PULLBACK (已追高，等回踩 KC 上軌)
       - 其餘 → HOLD
    修正原則：KC突破後動量往往已接近末段，應等待回踩 KC 軌道才進場，
    而非在突破高點追入，避免一開倉就面臨回落。
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

    def evaluate_signal(self, df: pd.DataFrame, ema_200_1h: float = None) -> dict:
        if len(df) < 50:
            return {"action": "HOLD", "reason": "Not enough data"}

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

        # --- 1. 底線防禦 (Mandatory Filters) ---
        # 如果這兩個不通過，分數直接為 0，絕對不開倉
        is_1h_bullish = (price >= ema_200_1h) if ema_200_1h is not None else True
        is_1h_bearish = (price <= ema_200_1h) if ema_200_1h is not None else True

        st_dir = curr['st_direction']

        # 底線判斷
        if st_dir == 1 and not is_1h_bullish:
            return {"action": "HOLD", "reason": "Mandatory_Fail: 1h_Trend_Bearish"}
        if st_dir == -1 and not is_1h_bearish:
            return {"action": "HOLD", "reason": "Mandatory_Fail: 1h_Trend_Bullish"}

        # 波動率過濾：ATR 佔價格比例太高或太低都不開倉。
        # 太高：SL/TP 用 ATR 倍數算出來的停損距離會被放大，同樣倉位金額下
        # 觸發止損時虧的錢遠大於移動止利能鎖住的獲利，是最大幾筆虧損的
        # 共同特徵。太低：市場太安靜時的「突破」更可能是盤整區間的假突破，
        # 沒有真實動能支撐，容易一進場就反轉（實測一批止損反推 ATR 只有
        # 0.07%~0.21%）。兩者一起框出一個波動適中的可交易區間。
        atr_pct = atr / price if price > 0 else 0
        if atr_pct > MAX_ATR_PCT:
            return {"action": "HOLD", "reason": f"Mandatory_Fail: ATR_Too_High({atr_pct:.2%})"}
        if atr_pct < MIN_ATR_PCT:
            return {"action": "HOLD", "reason": f"Mandatory_Fail: ATR_Too_Low({atr_pct:.2%})"}

        # --- 2. 動態評分系統 (Scoring System) ---
        score = 0
        score_details = []

        # A. Keltner 突破分數 (30分)
        kc_breakout_buffer = kc_width * KELTNER_BREAKOUT_MARGIN_PCT
        if st_dir == 1 and price >= (kc_upper + kc_breakout_buffer):
            score += 30
            score_details.append("KC_Breakout_Pass")
        elif st_dir == -1 and price <= (kc_lower - kc_breakout_buffer):
            score += 30
            score_details.append("KC_Breakout_Pass")
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

        # D. 訊號新鮮度分數 (連續淡化，滿分30分)：剛翻轉給滿分，隨根數線性
        # 淡化，到 FRESHNESS_DECAY_BARS 掃到 0 分，不是硬門檻全有全無。
        st_flip_age = bars_since_supertrend_flip(df['st_direction'])
        freshness_ratio = max(0.0, 1.0 - st_flip_age / FRESHNESS_DECAY_BARS) if FRESHNESS_DECAY_BARS > 0 else 0.0
        freshness_score = round(freshness_ratio * 30)
        score += freshness_score
        score_details.append(f"Freshness({st_flip_age}bars)+{freshness_score}")

        # E. 品質細分加分（0~9分）：讓同樣達標 70/80 分的訊號能再分出優劣，
        # 用於同一輪多個候選訊號時挑選最優的下單，而不是隨機/先到先進場。
        # 三項各佔 0~3 分，數值越好加分越多，實測跟虧損大小/勝率相關：
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
        # E2. RSI 強度：超出門檻越多代表動能越強（15分視為滿分）
        if st_dir == 1:
            rsi_margin = max(0.0, rsi - RSI_LONG_THRESHOLD)
        else:
            rsi_margin = max(0.0, RSI_SHORT_THRESHOLD - rsi)
        quality_bonus += round(min(rsi_margin / 15.0, 1.0) * 3)
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

        # --- 3. 回調狙擊最終決策 (Pullback Sniper Mode) ---
        # 修正核心：KC 突破是「訊號觸發」，等價格回踩 KC 軌道後才是「進場時機」
        # 進場門檻：總分 >= MIN_SCORE_THRESHOLD (90 分)
        # 額外防線：新鮮度子分數太低（趨勢已經很舊）直接擋單，不管總分靠
        # 其他項目湊得多高——避免「已經開始老化、快要反轉的趨勢尾端，
        # 靠其他項目湊夠分數壓線擠進場」這種樣貌。
        if score >= MIN_SCORE_THRESHOLD and freshness_score < MIN_FRESHNESS_SCORE:
            return {
                "action": "HOLD",
                "reason": f"Mandatory_Fail: Freshness_Too_Stale({st_flip_age}bars) | Score({score}) | {', '.join(score_details)}",
            }

        if score >= MIN_SCORE_THRESHOLD:
            if st_dir == 1:
                dist = (price - kc_upper) / kc_upper

                # 一律等回踩到目標區再進場，不再有「突破當下距離夠近就立刻
                # 追價買」的路徑——KC 通道倍數是拿來判斷「這是不是真突破」
                # 的確認門檻，跟「用什麼價格進場」是兩件事，進場價一律交給
                # 回踩機制決定，通道調寬也不會讓進場價跟著追高。
                # 回調目標：從 KC 上軌往 EMA20 均價再靠攏 PULLBACK_TARGET_DEPTH 比例。
                pullback_target = kc_upper - (kc_upper - ema_20) * PULLBACK_TARGET_DEPTH
                return {
                    "action": "WAIT_PULLBACK", "side": "LONG",
                    "price": price, "atr": atr,
                    "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                    "target_zone": pullback_target,
                    "reason": f"Pullback_WAIT({score}) | dist={dist:.2%} | Target={pullback_target:.4f} | {', '.join(score_details)}"
                }

            else:  # SHORT
                dist = (kc_lower - price) / kc_lower

                # 同理：空單也一律等反彈到目標區再進場。
                pullback_target = kc_lower + (ema_20 - kc_lower) * PULLBACK_TARGET_DEPTH
                return {
                    "action": "WAIT_PULLBACK", "side": "SHORT",
                    "price": price, "atr": atr,
                    "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                    "target_zone": pullback_target,
                    "reason": f"Pullback_WAIT({score}) | dist={dist:.2%} | Target={pullback_target:.4f} | {', '.join(score_details)}"
                }

        return {"action": "HOLD", "reason": f"Score_Low({score}) | {', '.join(score_details)}"}

    def confirm_pullback_entry(self, df: pd.DataFrame, side: str, ema_1h: float = None) -> dict:
        """回踩觸發當下的二次確認。

        訊號登記等待回踩時，可能已經是好幾分鐘甚至將近 PULLBACK_TIMEOUT_MINUTES
        分鐘前的舊資料；等價格真的回踩到目標區時，量能可能已經萎縮、RSI 可能已經
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
        st_dir = curr['st_direction']
        want_dir = 1 if side == "LONG" else -1

        if st_dir != want_dir:
            return {
                "status": "CANCEL",
                "reason": f"SuperTrend 方向已反轉（現為{'多頭' if st_dir == 1 else '空頭'}）",
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

        if vol_ma_20 > 0 and vol < (vol_ma_20 * KELTNER_MIN_VOLUME_RATIO):
            return {
                "status": "CANCEL",
                "reason": f"量能轉弱 {vol / vol_ma_20:.2f}x < {KELTNER_MIN_VOLUME_RATIO:.2f}x",
            }

        if side == "LONG" and rsi < RSI_LONG_THRESHOLD:
            return {"status": "CANCEL", "reason": f"RSI 轉弱 {rsi:.1f} < {RSI_LONG_THRESHOLD}"}
        if side == "SHORT" and rsi > RSI_SHORT_THRESHOLD:
            return {"status": "CANCEL", "reason": f"RSI 轉弱 {rsi:.1f} > {RSI_SHORT_THRESHOLD}"}

        return {"status": "PASS", "reason": "二次確認通過"}
