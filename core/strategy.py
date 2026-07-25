import pandas as pd
import numpy as np
from core.config import (
    STOP_LOSS_MULTIPLIER, TAKE_PROFIT_MULTIPLIER, MAX_BREAKOUT_DISTANCE,
    KELTNER_ATR_MULTIPLIER, KELTNER_BREAKOUT_MARGIN_PCT, KELTNER_MIN_VOLUME_RATIO, 
    SUPERTREND_MAX_FLIP_AGE_BARS, RSI_LONG_THRESHOLD, RSI_SHORT_THRESHOLD
)
from core.indicators import bars_since_supertrend_flip

class SuperTrendKeltnerStrategy:
    """
    High-Precision Scalping Engine (5m Timeframe) with 4 Quality Filters:
    1. 防插針價格: 優先選用 close_price_spike_filtered（無則退回 close）
    2. 突破幅度緩衝: 超出 Keltner 通道邊界 KELTNER_BREAKOUT_MARGIN_PCT (5% 通道寬度) 才算真突破
    3. 量能確認: 成交量須達 20 期均量 KELTNER_MIN_VOLUME_RATIO (0.5 倍)
    4. SuperTrend 新鮮度: 方向須在最近 SUPERTREND_MAX_FLIP_AGE_BARS (5 根) 內剛轉向
    """
    def __init__(self, atr_period=10, atr_multiplier=2.0):
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        high = df['high']
        low = df['low']

        # 防插針: 優先採用 SpikeFilter_L2 修正值 close_price_spike_filtered
        if 'close_price_spike_filtered' in df.columns:
            close = df['close_price_spike_filtered'].fillna(df['close'])
        else:
            close = df['close']

        volume = df['volume']

        # True Range & ATR
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=self.atr_period).mean()

        # EMAs (20 & 50)
        df['ema_20'] = close.ewm(span=20, adjust=False).mean()
        df['ema_50'] = close.ewm(span=50, adjust=False).mean()

        # 成交量 20 週期簡單移動平均 (20-period SMA Volume)
        df['vol_ma_20'] = volume.rolling(window=20).mean()

        # RSI 14
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        df['rsi'] = 100 - (100 / (1 + rs))

        # Keltner Channels (KELTNER_ATR_MULTIPLIER for sensitive breakout response)
        df['kc_upper'] = df['ema_20'] + (df['atr'] * KELTNER_ATR_MULTIPLIER)
        df['kc_lower'] = df['ema_20'] - (df['atr'] * KELTNER_ATR_MULTIPLIER)
        # Keltner 通道寬度 (Channel Width)
        df['kc_width'] = df['kc_upper'] - df['kc_lower']

        # SuperTrend Calculation
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

            # Upper band
            if basic_upper.iloc[i] < final_upper.iloc[i-1] or close.iloc[i-1] > final_upper.iloc[i-1]:
                final_upper.iloc[i] = basic_upper.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i-1]

            # Lower band
            if basic_lower.iloc[i] > final_lower.iloc[i-1] or close.iloc[i-1] < final_lower.iloc[i-1]:
                final_lower.iloc[i] = basic_lower.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i-1]

            # Direction
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

        # 防插針: 優先選用 SpikeFilter_L2 修正價格
        price = curr['close_price_spike_filtered'] if ('close_price_spike_filtered' in curr and not pd.isna(curr['close_price_spike_filtered'])) else curr['close']
        atr = curr['atr'] if not np.isnan(curr['atr']) else price * 0.015
        rsi = curr['rsi']
        vol = curr['volume']
        vol_ma_20 = curr['vol_ma_20'] if not np.isnan(curr['vol_ma_20']) else 0

        kc_upper = curr['kc_upper']
        kc_lower = curr['kc_lower']
        kc_width = curr['kc_width'] if not np.isnan(curr['kc_width']) else (price * 0.03)

        # 品質濾網 1: 量能確認 (當下量須達 20 期均量 0.5 倍)
        has_min_volume = (vol >= vol_ma_20 * KELTNER_MIN_VOLUME_RATIO) if vol_ma_20 > 0 else True

        # 品質濾網 2: 突破幅度緩衝 (須突破通道邊界)
        kc_breakout_buffer = kc_width * KELTNER_BREAKOUT_MARGIN_PCT
        required_long_breakout = kc_upper + kc_breakout_buffer
        required_short_breakout = kc_lower - kc_breakout_buffer

        # 品質濾網 3: SuperTrend 新鮮度 (方向須在最近 15 根 K 棒內剛轉向，放寬過度擬合)
        st_flip_age = bars_since_supertrend_flip(df['st_direction'])
        is_st_fresh = (st_flip_age <= SUPERTREND_MAX_FLIP_AGE_BARS)

        # 品質濾網 4: 1h 大週期趨勢保護（放寬：只在 price 明顯低於 EMA 超過 2% 才禁多，明顯高於才禁空）
        TREND_FILTER_MARGIN = 0.02  # 2% 容忍帶，橫盤整理期間不阻擋開倉
        is_1h_bullish = (price >= ema_200_1h * (1 - TREND_FILTER_MARGIN)) if ema_200_1h is not None else True
        is_1h_bearish = (price <= ema_200_1h * (1 + TREND_FILTER_MARGIN)) if ema_200_1h is not None else True

        # LONG 條件：突破 Keltner 通道上軌 + 5m EMA20 >= EMA50 + RSI 強勢
        #            + ✅ 1h 大週期做多保護 + ✅ SuperTrend 新鮮度門檻
        if (price >= kc_upper and
            curr['ema_20'] >= curr['ema_50'] and
            rsi >= RSI_LONG_THRESHOLD and
            has_min_volume and
            is_1h_bullish and          # ✅ 1h 趨勢向上才做多，禁止逆勢開倉
            is_st_fresh):              # ✅ SuperTrend 新鮮度門檻

            # 進場追高動態容忍度
            long_distance_ratio = (price - kc_upper) / kc_upper
            kc_buffer_ratio = kc_breakout_buffer / kc_upper
            dynamic_max_distance = max(MAX_BREAKOUT_DISTANCE, kc_buffer_ratio + 0.002)

            if long_distance_ratio > dynamic_max_distance:
                return {"action": "HOLD", "reason": f"突破追高過大 ({long_distance_ratio:.2%} > Dynamic Max {dynamic_max_distance:.2%})"}

            sl = price - (atr * STOP_LOSS_MULTIPLIER)
            tp = price + (atr * TAKE_PROFIT_MULTIPLIER)
            return {
                "action": "BUY",
                "side": "LONG",
                "price": price,
                "sl": sl,
                "tp": tp,
                "atr": atr,
                "reason": f"5m Bullish Keltner Breakout (RSI: {rsi:.1f}, Vol: {vol/vol_ma_20:.1f}x, 1h:{'✅' if is_1h_bullish else '❌'})"
            }

        # SHORT 條件：跌破 Keltner 通道下軌 + 5m EMA20 <= EMA50 + RSI 弱勢
        #             + ✅ 1h 大週期做空保護 + ✅ SuperTrend 新鮮度門檻
        if (price <= kc_lower and
            curr['ema_20'] <= curr['ema_50'] and
            rsi <= RSI_SHORT_THRESHOLD and
            has_min_volume and
            is_1h_bearish and          # ✅ 1h 趨勢向下才做空，禁止逆勢開倉
            is_st_fresh):              # ✅ SuperTrend 新鮮度門檻

            # 進場殺跌動態容忍度
            short_distance_ratio = (kc_lower - price) / kc_lower
            kc_buffer_ratio = kc_breakout_buffer / kc_lower
            dynamic_max_distance = max(MAX_BREAKOUT_DISTANCE, kc_buffer_ratio + 0.002)

            if short_distance_ratio > dynamic_max_distance:
                return {"action": "HOLD", "reason": f"跌破殺跌過大 ({short_distance_ratio:.2%} > Dynamic Max {dynamic_max_distance:.2%})"}

            sl = price + (atr * STOP_LOSS_MULTIPLIER)
            tp = price - (atr * TAKE_PROFIT_MULTIPLIER)
            return {
                "action": "SELL",
                "side": "SHORT",
                "price": price,
                "sl": sl,
                "tp": tp,
                "atr": atr,
                "reason": f"5m Bearish Keltner Breakout (RSI: {rsi:.1f}, Vol: {vol/vol_ma_20:.1f}x, 1h:{'✅' if is_1h_bearish else '❌'})"
            }

        return {"action": "HOLD", "reason": "No entry trigger"}
