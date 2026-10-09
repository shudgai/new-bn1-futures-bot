import ccxt
import pandas as pd
import numpy as np
import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

def calculate_kc(df, length=20, mult=2.0):
    df['ema_20'] = df['close'].ewm(span=length, adjust=False).mean()
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['atr'] = true_range.ewm(span=length, adjust=False).mean()
    df['kc_middle'] = df['ema_20']
    df['kc_upper'] = df['kc_middle'] + df['atr'] * mult
    df['kc_lower'] = df['kc_middle'] - df['atr'] * mult
    return df

exchange = ccxt.binanceusdm()
ohlcv = exchange.fetch_ohlcv('NEIROUSDT', timeframe='1m', limit=100)
df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
df = calculate_kc(df, length=20, mult=2.0)
df['ma5'] = df['close'].rolling(5).mean()
df['ma15'] = df['close'].rolling(15).mean()
df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')

# Find where it was inside KC then crossed KC upper
cols = ['datetime', 'open', 'high', 'low', 'close', 'kc_upper', 'kc_lower', 'ma5', 'ma15', 'atr']
# We want data from 2026-10-04 23:10 to 23:15
df_recent = df.tail(15)
for _, row in df_recent.iterrows():
    print(f"{row['datetime']} | O: {row['open']:.6f} H: {row['high']:.6f} L: {row['low']:.6f} C: {row['close']:.6f} | KC_U: {row['kc_upper']:.6f} KC_L: {row['kc_lower']:.6f} | MA5: {row['ma5']:.6f} MA15: {row['ma15']:.6f} ATR: {row['atr']:.8f}")
