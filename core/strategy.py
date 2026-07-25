import pandas as pd
import numpy as np
from core.config import STOP_LOSS_MULTIPLIER, TAKE_PROFIT_MULTIPLIER, MAX_BREAKOUT_DISTANCE

class SuperTrendKeltnerStrategy:
    """
    High-Precision Scalping Engine (5m Timeframe):
    - SuperTrend (ATR multiplier 2.0, period 10)
    - Keltner Channel Breakout (EMA 20, ATR multiplier 1.5 for stronger boundary)
    - Volume Surge Filter (Current Vol > 1.2x 50-SMA Volume to avoid fake breakouts)
    - Strict RSI Filter (Long RSI >= 52, Short RSI <= 48)
    - Entry Breakout Distance Filter (MAX_BREAKOUT_DISTANCE = 0.3%)
    - Dynamic Risk Control: SL = 1.8x ATR, TP = 2.2x ATR
    """
    def __init__(self, atr_period=10, atr_multiplier=2.0):
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        high = df['high']
        low = df['low']
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

        # Volume 50-period Simple Moving Average
        df['vol_ma'] = volume.rolling(window=50).mean()

        # RSI 14
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        df['rsi'] = 100 - (100 / (1 + rs))

        # Keltner Channels (Strengthened to 1.5 multiplier)
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
        price = curr['close']
        atr = curr['atr'] if not np.isnan(curr['atr']) else price * 0.015
        rsi = curr['rsi']
        vol = curr['volume']
        vol_ma = curr['vol_ma'] if not np.isnan(curr['vol_ma']) else 0

        kc_upper = curr['kc_upper']
        kc_lower = curr['kc_lower']

        # 1. 爆量確認：成交量需大於 50 週期均量的 1.15 倍 (防無效低量假突破)
        has_volume_surge = (vol >= vol_ma * 1.15) if vol_ma > 0 else True

        # Long: SuperTrend Bullish AND Price >= Keltner Upper AND EMA 20 >= EMA 50 AND RSI >= 52 AND Volume Surge
        if curr['st_direction'] == 1 and curr['close'] >= kc_upper and curr['ema_20'] >= curr['ema_50'] and rsi >= 52 and has_volume_surge:
            # 進場突破距離過濾 (避免追高 > 0.3%)
            long_distance_ratio = (price - kc_upper) / kc_upper
            if long_distance_ratio > MAX_BREAKOUT_DISTANCE:
                return {"action": "HOLD", "reason": f"突破追高過大 ({long_distance_ratio:.2%} > Max {MAX_BREAKOUT_DISTANCE:.2%})"}

            sl = price - (atr * STOP_LOSS_MULTIPLIER)
            tp = price + (atr * TAKE_PROFIT_MULTIPLIER)
            return {
                "action": "BUY",
                "side": "LONG",
                "price": price,
                "sl": sl,
                "tp": tp,
                "atr": atr,
                "reason": f"5m Strong Bullish Breakout (VolSurge, Dist: {long_distance_ratio:.2%})"
            }

        # Short: SuperTrend Bearish AND Price <= Keltner Lower AND EMA 20 <= EMA 50 AND RSI <= 48 AND Volume Surge
        if curr['st_direction'] == -1 and curr['close'] <= kc_lower and curr['ema_20'] <= curr['ema_50'] and rsi <= 48 and has_volume_surge:
            # 進場跌破距離過濾 (避免殺跌 > 0.3%)
            short_distance_ratio = (kc_lower - price) / kc_lower
            if short_distance_ratio > MAX_BREAKOUT_DISTANCE:
                return {"action": "HOLD", "reason": f"跌破殺跌過大 ({short_distance_ratio:.2%} > Max {MAX_BREAKOUT_DISTANCE:.2%})"}

            sl = price + (atr * STOP_LOSS_MULTIPLIER)
            tp = price - (atr * TAKE_PROFIT_MULTIPLIER)
            return {
                "action": "SELL",
                "side": "SHORT",
                "price": price,
                "sl": sl,
                "tp": tp,
                "atr": atr,
                "reason": f"5m Strong Bearish Breakout (VolSurge, Dist: {short_distance_ratio:.2%})"
            }

        return {"action": "HOLD", "reason": "No entry trigger"}
