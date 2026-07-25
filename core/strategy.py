import pandas as pd
import numpy as np

class SuperTrendKeltnerStrategy:
    """
    New Quantitative Strategy Engine:
    - SuperTrend (ATR multiplier 3.0, period 10)
    - Keltner Channel Breakout (EMA 20, ATR multiplier 1.5)
    - Dynamic 50/200 Trend Bias Filter
    """
    def __init__(self, atr_period=10, atr_multiplier=3.0):
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        high = df['high']
        low = df['low']
        close = df['close']

        # True Range & ATR
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=self.atr_period).mean()

        # EMAs
        df['ema_20'] = close.ewm(span=20, adjust=False).mean()
        df['ema_50'] = close.ewm(span=50, adjust=False).mean()
        df['ema_200'] = close.ewm(span=200, adjust=False).mean()

        # Keltner Channels
        df['kc_upper'] = df['ema_20'] + (df['atr'] * 1.5)
        df['kc_lower'] = df['ema_20'] - (df['atr'] * 1.5)

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

    def evaluate_signal(self, df: pd.DataFrame) -> dict:
        if len(df) < 50:
            return {"action": "HOLD", "reason": "Not enough data"}

        df = self.compute_indicators(df)
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        price = curr['close']
        atr = curr['atr'] if not np.isnan(curr['atr']) else price * 0.015

        # Signal rules
        # Long: SuperTrend turns bullish AND price breaks above Keltner Upper AND EMA 50 > EMA 200
        if curr['st_direction'] == 1 and curr['close'] > curr['kc_upper'] and curr['ema_50'] >= curr['ema_200']:
            sl = price - (atr * 1.5)
            tp = price + (atr * 3.0)
            return {
                "action": "BUY",
                "side": "LONG",
                "price": price,
                "sl": sl,
                "tp": tp,
                "atr": atr,
                "reason": "SuperTrend Bullish + Keltner Upper Breakout"
            }

        # Short: SuperTrend turns bearish AND price breaks below Keltner Lower AND EMA 50 <= EMA 200
        if curr['st_direction'] == -1 and curr['close'] < curr['kc_lower'] and curr['ema_50'] <= curr['ema_200']:
            sl = price + (atr * 1.5)
            tp = price - (atr * 3.0)
            return {
                "action": "SELL",
                "side": "SHORT",
                "price": price,
                "sl": sl,
                "tp": tp,
                "atr": atr,
                "reason": "SuperTrend Bearish + Keltner Lower Breakout"
            }

        return {"action": "HOLD", "reason": "No entry trigger"}
