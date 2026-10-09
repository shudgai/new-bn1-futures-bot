import pandas as pd
import numpy as np
import math

df = pd.read_csv('scratch/lobster_real_1m_history.csv')
# The user said final 1500 are dev, so use the first 99500 for OOS
df_oos = df.iloc[:99500].copy()

# Base indicators
df_oos['ma5'] = df_oos['close'].rolling(5).mean()
df_oos['ma15'] = df_oos['close'].rolling(15).mean()
df_oos['kc_mid'] = df_oos['close'].rolling(20).mean()

# To handle 5m correctly: 
# Map each 1m timestamp to the start of its most recently CLOSED 5m candle.
# If current is 10:07:00 (1791030420000), closed 5m is 10:00:00 (timestamp 1791030000000)
df_oos['closed_5m_ts'] = (df_oos['timestamp'] // 300000) * 300000 - 300000

# Resample to 5m to calculate 5m indicators
df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
df_5m = df.set_index('datetime').resample('5min').agg({
    'timestamp': 'first',
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last',
    'volume': 'sum'
}).dropna().reset_index(drop=True)

df_5m['ma5'] = df_5m['close'].rolling(5).mean()
df_5m['ma15'] = df_5m['close'].rolling(15).mean()
df_5m['ma5_prev'] = df_5m['ma5'].shift(1)

# Merge 5m data onto 1m by 'closed_5m_ts' == 'timestamp'
df_5m_subset = df_5m[['timestamp', 'ma5', 'ma15', 'ma5_prev']].rename(
    columns={'timestamp': 'closed_5m_ts', 'ma5': '5m_ma5', 'ma15': '5m_ma15', 'ma5_prev': '5m_ma5_prev'}
)
df_oos = pd.merge(df_oos, df_5m_subset, on='closed_5m_ts', how='left')

# Print basic stats to prove it worked
print(f"OOS_BARS={len(df_oos)}")
print("Script execution successful.")
