import pandas as pd
import pandas_ta as ta

df_cr = pd.read_csv('scratch/pepe_5m.csv', names=['timestamp', 'open', 'close'])
df_cr['high'] = df_cr[['open', 'close']].max(axis=1) + 0.000010
df_cr['low'] = df_cr[['open', 'close']].min(axis=1) - 0.000010
df_cr['ma3'] = ta.sma(df_cr['close'], length=3)

ma3_curr = float(df_cr['ma3'].iloc[-1])
ma3_prev = float(df_cr['ma3'].iloc[-2])
last_close = float(df_cr['close'].iloc[-1])
last_open = float(df_cr['open'].iloc[-1])

print(f"ma3_curr: {ma3_curr:.8f}")
print(f"ma3_prev: {ma3_prev:.8f}")
print(f"last_close: {last_close:.8f}")
print(f"Condition: ma3_curr > ma3_prev and last_close > ma3_curr")
print(f"Result: {ma3_curr > ma3_prev and last_close > ma3_curr}")
