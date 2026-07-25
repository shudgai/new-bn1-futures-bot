import pandas as pd
import numpy as np
from core.config import (
    STOP_LOSS_MULTIPLIER, TAKE_PROFIT_MULTIPLIER, MAX_BREAKOUT_DISTANCE,
    KELTNER_BREAKOUT_MARGIN_PCT, KELTNER_MIN_VOLUME_RATIO, SUPERTREND_MAX_FLIP_AGE_BARS,
    RSI_LONG_THRESHOLD, RSI_SHORT_THRESHOLD,
    MIN_SCORE_THRESHOLD, PULLBACK_ZONE_PCT
)
from core.indicators import bars_since_supertrend_flip

class SuperTrendKeltnerStrategy:
    """
    高精度量化引擎 - 精準狙擊版本 (Sniper Mode)
    核心邏輯：
    1. 底線防禦 (Mandatory)：大週期趨勢 (1h EMA50) 與 SuperTrend 方向必須一致。
    2. 動態評分 (Scoring)：Keltner 突破、量能、RSI、訊號新鮮度 進行加權評分。
    3. 進場決策三段式：
       - 評分 >= 90 且追高距離 <= 0.2% → BUY_NOW  (立即開倉)
       - 評分 >= 90 且追高距離 <= 0.7% → WAIT_PULLBACK (回調待命)
       - 其餘 → HOLD
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

        # D. 訊號新鮮度分數 (30分)
        st_flip_age = bars_since_supertrend_flip(df['st_direction'])
        if st_flip_age <= SUPERTREND_MAX_FLIP_AGE_BARS:
            score += 30
            score_details.append("Freshness_Pass")
        else:
            score_details.append("Freshness_Fail")

        # --- 3. 精準狙擊最終決策 (Sniper Mode) ---
        # 進場門檻：總分 >= MIN_SCORE_THRESHOLD (90 分)
        if score >= MIN_SCORE_THRESHOLD:
            if st_dir == 1:
                dist = (price - kc_upper) / kc_upper

                if dist <= MAX_BREAKOUT_DISTANCE:
                    # ✅ A段：評分足且進場點極佳 → 立即開倉
                    sl = price - (atr * STOP_LOSS_MULTIPLIER)
                    tp = price + (atr * TAKE_PROFIT_MULTIPLIER)
                    return {
                        "action": "BUY", "side": "LONG", "price": price,
                        "sl": sl, "tp": tp, "atr": atr,
                        "kc_upper": kc_upper, "kc_lower": kc_lower,
                        "reason": f"Sniper_BUY_NOW({score}) | dist={dist:.2%} | {', '.join(score_details)}"
                    }
                elif dist <= (MAX_BREAKOUT_DISTANCE + 0.005):
                    # ⏳ B段：評分足但价格稍高，進入「回調待命」狀態
                    return {
                        "action": "WAIT_PULLBACK", "side": "LONG",
                        "price": price, "atr": atr,
                        "kc_upper": kc_upper, "kc_lower": kc_lower,
                        "target_zone": kc_upper,  # 回調目標：KC 上軌附近
                        "reason": f"Sniper_WAIT_PULLBACK({score}) | dist={dist:.2%} | {', '.join(score_details)}"
                    }
                else:
                    return {"action": "HOLD", "reason": f"Score_Pass({score}), but Chase_Limit_Exceeded({dist:.2%})"}

            else:  # SHORT
                dist = (kc_lower - price) / kc_lower

                if dist <= MAX_BREAKOUT_DISTANCE:
                    # ✅ A段：評分足且進場點極佳 → 立即開倉
                    sl = price + (atr * STOP_LOSS_MULTIPLIER)
                    tp = price - (atr * TAKE_PROFIT_MULTIPLIER)
                    return {
                        "action": "SELL", "side": "SHORT", "price": price,
                        "sl": sl, "tp": tp, "atr": atr,
                        "kc_upper": kc_upper, "kc_lower": kc_lower,
                        "reason": f"Sniper_SELL_NOW({score}) | dist={dist:.2%} | {', '.join(score_details)}"
                    }
                elif dist <= (MAX_BREAKOUT_DISTANCE + 0.005):
                    # ⏳ B段：評分足但价格稍低，進入「回調待命」狀態
                    return {
                        "action": "WAIT_PULLBACK", "side": "SHORT",
                        "price": price, "atr": atr,
                        "kc_upper": kc_upper, "kc_lower": kc_lower,
                        "target_zone": kc_lower,  # 回調目標：KC 下軌附近
                        "reason": f"Sniper_WAIT_PULLBACK({score}) | dist={dist:.2%} | {', '.join(score_details)}"
                    }
                else:
                    return {"action": "HOLD", "reason": f"Score_Pass({score}), but Chase_Limit_Exceeded({dist:.2%})"}

        return {"action": "HOLD", "reason": f"Score_Low({score}) | {', '.join(score_details)}"}

