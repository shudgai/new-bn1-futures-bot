import pandas as pd
df = pd.read_csv("scratch/lobster_real_1m_history.csv")
df = df[(df["timestamp"] >= 1790967900000) & (df["timestamp"] <= 1790968380000)]
for _, r in df.iterrows():
    print(f"Time={r['timestamp']} O={r['open']} H={r['high']} L={r['low']} C={r['close']}")
