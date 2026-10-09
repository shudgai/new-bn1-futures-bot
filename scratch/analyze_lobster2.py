import pandas as pd
df = pd.read_csv("scratch/lobster_real_1m_history.csv")
df = df[(df['timestamp'] >= 1790968000000) & (df['timestamp'] <= 1790969000000)]
print(df[['timestamp', 'open', 'high', 'low', 'close']])
