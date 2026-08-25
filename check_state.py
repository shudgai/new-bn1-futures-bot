import ccxt
import pandas as pd
import numpy as np

exchange = ccxt.binanceusdm()
klines = exchange.fetch_ohlcv('1000PEPE/USDT', '5m', limit=100)
df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
for col in ['open', 'high', 'low', 'close', 'volume']:
    df[col] = df[col].astype(float)

# Drop unclosed candle
df = df.iloc[:-1]

df['ma5'] = df['close'].rolling(5).mean()
df['ma25'] = df['close'].rolling(25).mean()

adx_period = 14
high = df['high']
low = df['low']
close = df['close']

tr1 = high - low
tr2 = (high - close.shift(1)).abs()
tr3 = (low - close.shift(1)).abs()
tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

up_move = high.diff()
down_move = -low.diff()
plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

tr_smooth = tr.ewm(alpha=1 / adx_period, adjust=False).mean()
plus_di = 100 * (plus_dm.ewm(alpha=1 / adx_period, adjust=False).mean() / (tr_smooth + 1e-9))
minus_di = 100 * (minus_dm.ewm(alpha=1 / adx_period, adjust=False).mean() / (tr_smooth + 1e-9))
dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
df['adx'] = dx.ewm(alpha=1 / adx_period, adjust=False).mean()

ma5_curr = float(df['ma5'].iloc[-1])
ma5_prev = float(df['ma5'].iloc[-2])
ma25_curr = float(df['ma25'].iloc[-1])
ma25_prev = float(df['ma25'].iloc[-2])
adx_curr = float(df['adx'].iloc[-1])

print(f"MA5: {ma5_curr:.6g}, MA25: {ma25_curr:.6g}")
print(f"ADX: {adx_curr:.2f}")
if ma5_curr > ma25_curr:
    print("Trend: LONG (MA5 > MA25)")
elif ma5_curr < ma25_curr:
    print("Trend: SHORT (MA5 < MA25)")
