import pandas as pd
import pandas_ta as ta

df_cr = pd.read_csv('scratch/pepe_1m.csv', names=['timestamp', 'open', 'close'])
df_cr['timestamp'] = pd.to_datetime(df_cr['timestamp'], unit='ms')
df_cr['timestamp'] = df_cr['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Taipei')
df_cr['ma3'] = ta.sma(df_cr['close'], length=3)
print(df_cr.tail(5))
