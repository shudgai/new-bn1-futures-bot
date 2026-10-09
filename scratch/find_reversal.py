import pandas as pd
df = pd.read_csv("scratch/lobster_real_1m_history.csv")
df['ma3'] = df['close'].rolling(3).mean()
df['ma5'] = df['close'].rolling(5).mean()
matches = df[(df['open'] == 0.04983) & (df['close'] == 0.04963)]
if not matches.empty:
    idx = matches.index[0]
    for i in range(idx-4, idx+1):
        print(df.iloc[i].to_dict())
