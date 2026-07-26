import pandas as pd
import numpy as np
from core.config import (
    STOP_LOSS_MULTIPLIER, TAKE_PROFIT_MULTIPLIER, MAX_BREAKOUT_DISTANCE,
    KELTNER_BREAKOUT_MARGIN_PCT, KELTNER_MIN_VOLUME_RATIO, SUPERTREND_MAX_FLIP_AGE_BARS,
    KELTNER_PARTIAL_VOLUME_RATIO,
    RSI_LONG_THRESHOLD, RSI_SHORT_THRESHOLD, RSI_LONG_PARTIAL_THRESHOLD, RSI_SHORT_PARTIAL_THRESHOLD,
    FAST_ENTRY_MODE, MIN_SCORE_THRESHOLD, PULLBACK_ZONE_PCT,
    PULLBACK_CONFIRM_RSI_LONG, PULLBACK_CONFIRM_RSI_SHORT
)
from core.indicators import bars_since_supertrend_flip

class SuperTrendKeltnerStrategy:
    """
    SuperTrend + Keltner 交易策略。

    FAST_ENTRY_MODE 開啟時，進場條件對齊 Port 8005：
    Keltner 突破、SuperTrend 方向與新鮮度、EMA20/EMA50、
    成交量及 RSI 全部通過後立即進場。關閉時則保留原本的回調等待模式。
    """
    def __init__(self, atr_period=10, atr_multiplier=3.0):
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier

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

        # Keltner Channels
        df['kc_upper'] = df['ema_20'] + (df['atr'] * 1.5)
        df['kc_lower'] = df['ema_20'] - (df['atr'] * 1.5)
        df['kc_width'] = df['kc_upper'] - df['kc_lower']

        # SuperTrend
        hl2 = (high + low) / 2
        basic_upper = hl2 + (self.atr_multiplier * df['atr'])
        basic_lower = hl2 - (self.atr_multiplier * df['atr'])

        final_upper = pd.Series(index=df.index, dtype=float)
        final_lower = pd.Series(index=df.index, dtype=float)
        supertrend = pd.Series(index=df.index, dtype=float)
        direction = pd.Series(index=df.index, dtype=int)

        for i in range(len(df)):
            if i == 0:
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

    def validate_pullback_entry(
        self, df: pd.DataFrame, side: str, live_price: float, ema_1h: float = None,
        signal_score: int = None,
    ) -> dict:
        """回調真正成交前，以最近一根已收 K 再驗證趨勢與反彈／反壓確認。"""
        if df is None or len(df) < 50:
            return {"status": "WAIT", "reason": "K線資料不足"}

        checked = self.compute_indicators(df)
        if len(checked) < 3:
            return {"status": "WAIT", "reason": "確認資料不足"}

        # -1 是尚未收完的即時 K；使用 -2 避免盤中指標反覆變動。
        curr = checked.iloc[-2]
        prev = checked.iloc[-3]
        close = (
            curr['close_price_spike_filtered']
            if 'close_price_spike_filtered' in checked.columns
            and not pd.isna(curr.get('close_price_spike_filtered'))
            else curr['close']
        )
        prev_close = (
            prev['close_price_spike_filtered']
            if 'close_price_spike_filtered' in checked.columns
            and not pd.isna(prev.get('close_price_spike_filtered'))
            else prev['close']
        )
        vol_ma = curr.get('vol_ma_20', 0)
        volume_pass = (
            not pd.isna(vol_ma) and vol_ma > 0
            and curr['volume'] >= vol_ma * KELTNER_MIN_VOLUME_RATIO
        )
        flip_age = bars_since_supertrend_flip(checked['st_direction'].iloc[:-1])
        freshness_pass = flip_age <= SUPERTREND_MAX_FLIP_AGE_BARS

        failures = []
        if side == "LONG":
            if curr['st_direction'] != 1:
                failures.append("SuperTrend 已轉空")
            if ema_1h is not None and live_price < ema_1h:
                failures.append("跌破 1h EMA50")
            if curr['rsi'] < PULLBACK_CONFIRM_RSI_LONG:
                failures.append(f"RSI {curr['rsi']:.1f} < {PULLBACK_CONFIRM_RSI_LONG}")
            if curr['ema_20'] <= prev['ema_20']:
                failures.append("EMA20 斜率未向上")
        elif side == "SHORT":
            if curr['st_direction'] != -1:
                failures.append("SuperTrend 已轉多")
            if ema_1h is not None and live_price > ema_1h:
                failures.append("升破 1h EMA50")
            if curr['rsi'] > PULLBACK_CONFIRM_RSI_SHORT:
                failures.append(f"RSI {curr['rsi']:.1f} > {PULLBACK_CONFIRM_RSI_SHORT}")
            if curr['ema_20'] >= prev['ema_20']:
                failures.append("EMA20 斜率未向下")
        else:
            failures.append(f"未知方向 {side}")

        if not volume_pass:
            failures.append("量能低於 0.8 倍均量")
        if not freshness_pass:
            failures.append(f"SuperTrend 已過期 ({flip_age} 根)")
        if failures:
            return {"status": "CANCEL", "reason": "；".join(failures)}

        if side == "LONG":
            confirmed = live_price >= curr['kc_upper'] and close > prev_close
            wait_reason = "等待重新站上 KC 上軌且已收 K 轉強"
        else:
            confirmed = live_price <= curr['kc_lower'] and close < prev_close
            wait_reason = "等待重新跌破 KC 下軌且已收 K 轉弱"
        if not confirmed:
            return {"status": "WAIT", "reason": wait_reason}

        return {
            "status": "PASS",
            "reason": (
                f"{'70分低風險回調、' if signal_score is not None and signal_score < 80 else ''}"
                f"ST新鮮({flip_age})、1h趨勢、KC重新確認、"
                f"RSI={curr['rsi']:.1f}、量能={curr['volume'] / vol_ma:.2f}x、EMA20斜率通過"
            ),
            "atr": float(curr['atr']),
        }

    def evaluate_signal(self, df: pd.DataFrame, ema_200_1h: float = None) -> dict:
        if len(df) < 50:
            return {"action": "HOLD", "reason": "Not enough data"}

        df = self.compute_indicators(df)
        curr = df.iloc[-1]

        # --- 基本數據提取 ---
        price = curr['close_price_spike_filtered'] if ('close_price_spike_filtered' in curr and not pd.isna(curr['close_price_spike_filtered'])) else curr['close']
        atr = curr['atr'] if not np.isnan(curr['atr']) else price * 0.015
        rsi = curr['rsi']
        vol = curr['volume']
        vol_ma_20 = curr['vol_ma_20'] if not np.isnan(curr['vol_ma_20']) else 0
        kc_upper = curr['kc_upper']
        kc_lower = curr['kc_lower']
        kc_width = curr['kc_width'] if not np.isnan(curr['kc_width']) else (price * 0.03)

        # 快速突破模式與8005一致，使用5m EMA20/EMA50波段方向；
        # 舊回調模式仍保留1h EMA50總趨勢過濾。
        st_dir = curr['st_direction']
        if FAST_ENTRY_MODE:
            trend_long = curr['ema_20'] >= curr['ema_50']
            trend_short = curr['ema_20'] <= curr['ema_50']
            if st_dir == 1 and not trend_long:
                return {"action": "HOLD", "reason": "Mandatory_Fail: EMA20_Below_EMA50"}
            if st_dir == -1 and not trend_short:
                return {"action": "HOLD", "reason": "Mandatory_Fail: EMA20_Above_EMA50"}
        else:
            is_1h_bullish = (price >= ema_200_1h) if ema_200_1h is not None else True
            is_1h_bearish = (price <= ema_200_1h) if ema_200_1h is not None else True
            if st_dir == 1 and not is_1h_bullish:
                return {"action": "HOLD", "reason": "Mandatory_Fail: 1h_Trend_Bearish"}
            if st_dir == -1 and not is_1h_bearish:
                return {"action": "HOLD", "reason": "Mandatory_Fail: 1h_Trend_Bullish"}

        # --- 2. 動態評分系統 (Scoring System) ---
        score = 0
        score_details = []

        # A. Keltner 突破分數 (30分)
        kc_breakout_buffer = kc_width * KELTNER_BREAKOUT_MARGIN_PCT
        kc_breakout_pass = False
        if st_dir == 1 and price >= (kc_upper + kc_breakout_buffer):
            kc_breakout_pass = True
            score += 30
            score_details.append("KC_Breakout_Pass")
        elif st_dir == -1 and price <= (kc_lower - kc_breakout_buffer):
            kc_breakout_pass = True
            score += 30
            score_details.append("KC_Breakout_Pass")
        else:
            score_details.append("KC_Breakout_Fail")

        # B. 量能確認分數（完整 20 分、接近門檻 10 分）
        if vol_ma_20 > 0 and vol >= (vol_ma_20 * KELTNER_MIN_VOLUME_RATIO):
            score += 20
            score_details.append("Volume_Pass")
        elif vol_ma_20 > 0 and vol >= (vol_ma_20 * KELTNER_PARTIAL_VOLUME_RATIO):
            score += 10
            score_details.append("Volume_Partial")
        else:
            score_details.append("Volume_Fail")

        # C. RSI 強勢分數（完整 20 分、方向剛轉強 10 分）
        if st_dir == 1 and rsi >= RSI_LONG_THRESHOLD:
            score += 20
            score_details.append("RSI_Pass")
        elif st_dir == 1 and rsi >= RSI_LONG_PARTIAL_THRESHOLD:
            score += 10
            score_details.append("RSI_Partial")
        elif st_dir == -1 and rsi <= RSI_SHORT_THRESHOLD:
            score += 20
            score_details.append("RSI_Pass")
        elif st_dir == -1 and rsi <= RSI_SHORT_PARTIAL_THRESHOLD:
            score += 10
            score_details.append("RSI_Partial")
        else:
            score_details.append("RSI_Fail")

        # D. 訊號新鮮度分數 (30分)
        st_flip_age = bars_since_supertrend_flip(df['st_direction'])
        freshness_pass = st_flip_age <= SUPERTREND_MAX_FLIP_AGE_BARS
        if freshness_pass:
            score += 30
            score_details.append("Freshness_Pass")
        else:
            score_details.append("Freshness_Fail")

        # 兩者改為必要條件。舊邏輯在 KC 未突破時 dist 為負數，
        # 仍可能被「dist <= 0.1%」誤判為立即進場；過期 SuperTrend 也會以 70 分追價。
        if not kc_breakout_pass:
            return {
                "action": "HOLD",
                "reason": f"Mandatory_Fail: KC_Breakout | Score({score}) | {', '.join(score_details)}"
            }
        if not freshness_pass:
            return {
                "action": "HOLD",
                "reason": f"Mandatory_Fail: SuperTrend_Stale({st_flip_age}) | Score({score}) | {', '.join(score_details)}"
            }

        if FAST_ENTRY_MODE:
            volume_pass = vol_ma_20 > 0 and vol >= vol_ma_20 * KELTNER_MIN_VOLUME_RATIO
            rsi_pass = (
                st_dir == 1 and rsi >= RSI_LONG_THRESHOLD
            ) or (
                st_dir == -1 and rsi <= RSI_SHORT_THRESHOLD
            )
            if not volume_pass:
                return {
                    "action": "HOLD",
                    "reason": f"Mandatory_Fail: Volume_LT_{KELTNER_MIN_VOLUME_RATIO:.2f}x | Score({score})",
                }
            if not rsi_pass:
                return {
                    "action": "HOLD",
                    "reason": f"Mandatory_Fail: RSI_Direction | RSI({rsi:.1f}) | Score({score})",
                }
            sl = price - atr * STOP_LOSS_MULTIPLIER if st_dir == 1 else price + atr * STOP_LOSS_MULTIPLIER
            tp = price + atr * TAKE_PROFIT_MULTIPLIER if st_dir == 1 else price - atr * TAKE_PROFIT_MULTIPLIER
            return {
                "action": "BUY" if st_dir == 1 else "SELL",
                "side": "LONG" if st_dir == 1 else "SHORT",
                "price": price,
                "sl": sl,
                "tp": tp,
                "atr": atr,
                "kc_upper": kc_upper,
                "kc_lower": kc_lower,
                "score": score,
                "reason": (
                    f"Fast_Keltner_SuperTrend({score}) | ST新鮮({st_flip_age}) | "
                    f"EMA20/50同向 | RSI={rsi:.1f} | 量能={vol / vol_ma_20:.2f}x"
                ),
            }

        # --- 3. 回調狙擊最終決策 (Pullback Sniper Mode) ---
        # 修正核心：KC 突破是「訊號觸發」，等價格回踩 KC 軌道後才是「進場時機」
        # 70~79 分只能等待回調；80 分以上才允許在安全距離內立即進場。
        if score >= MIN_SCORE_THRESHOLD:
            if st_dir == 1:
                dist = (price - kc_upper) / kc_upper

                if score >= 80 and dist <= MAX_BREAKOUT_DISTANCE:
                    # ✅ A段：剛剛突破（距離極近 ≤ 0.1%），仍在安全進場點 → 立即開倉
                    sl = price - (atr * STOP_LOSS_MULTIPLIER)
                    tp = price + (atr * TAKE_PROFIT_MULTIPLIER)
                    return {
                        "action": "BUY", "side": "LONG", "price": price,
                        "sl": sl, "tp": tp, "atr": atr,
                        "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                        "reason": f"Pullback_BUY_NOW({score}) | dist={dist:.2%} | {', '.join(score_details)}"
                    }
                else:
                    # ⏳ B段（核心修正）：突破後價格已離 KC 上軌太遠 → 一律等回踩 KC 上軌再進場
                    # 回調目標：KC 上軌（突破後正常回踩的支撐位）
                    return {
                        "action": "WAIT_PULLBACK", "side": "LONG",
                        "price": price, "atr": atr,
                        "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                        "target_zone": kc_upper,  # 回調目標：KC 上軌（突破後的第一道支撐）
                        "reason": (
                            f"{'Pullback_WAIT_LOW_SCORE' if score < 80 else 'Pullback_WAIT'}({score}) | "
                            f"dist={dist:.2%} | KC_Upper={kc_upper:.4f} | {', '.join(score_details)}"
                        )
                    }

            else:  # SHORT
                dist = (kc_lower - price) / kc_lower

                if score >= 80 and dist <= MAX_BREAKOUT_DISTANCE:
                    # ✅ A段：剛剛跌破（距離極近 ≤ 0.1%），仍在安全進場點 → 立即開倉
                    sl = price + (atr * STOP_LOSS_MULTIPLIER)
                    tp = price - (atr * TAKE_PROFIT_MULTIPLIER)
                    return {
                        "action": "SELL", "side": "SHORT", "price": price,
                        "sl": sl, "tp": tp, "atr": atr,
                        "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                        "reason": f"Pullback_SELL_NOW({score}) | dist={dist:.2%} | {', '.join(score_details)}"
                    }
                else:
                    # ⏳ B段（核心修正）：跌破後價格已離 KC 下軌太遠 → 一律等反彈回 KC 下軌再做空
                    return {
                        "action": "WAIT_PULLBACK", "side": "SHORT",
                        "price": price, "atr": atr,
                        "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                        "target_zone": kc_lower,  # 回調目標：KC 下軌（跌破後的第一道阻力）
                        "reason": (
                            f"{'Pullback_WAIT_LOW_SCORE' if score < 80 else 'Pullback_WAIT'}({score}) | "
                            f"dist={dist:.2%} | KC_Lower={kc_lower:.4f} | {', '.join(score_details)}"
                        )
                    }

        return {"action": "HOLD", "reason": f"Score_Low({score}) | {', '.join(score_details)}"}

