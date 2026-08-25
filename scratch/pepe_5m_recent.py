import pandas as pd

df = pd.read_csv('scratch/pepe_5m.csv', names=['timestamp', 'open', 'close'])
df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Taipei')
df['ma3'] = df['close'].rolling(3).mean()
print(df.tail(15))
