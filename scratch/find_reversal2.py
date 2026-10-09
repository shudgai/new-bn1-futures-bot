import pandas as pd
df = pd.read_csv("scratch/lobster_real_1m_history.csv")
df['ma3'] = df['close'].rolling(3).mean()
df['ma5'] = df['close'].rolling(5).mean()
matches = df[(df['high'] > 0.05027) & (df['high'] < 0.05029) & (df['low'] > 0.04943) & (df['low'] < 0.04945)]
if not matches.empty:
    idx = matches.index[-1]
    for i in range(idx-4, idx+1):
        print(f"Index: {i}")
        for k, v in df.iloc[i].items():
            print(f"  {k}: {v}")
