import pandas as pd

df = pd.read_csv("scratch/lobster_real_1m_history.csv")
df["ma3"] = df["close"].rolling(3).mean()
df["ma5"] = df["close"].rolling(5).mean()

mask = (df["timestamp"] >= 1790945940000) & (df["timestamp"] <= 1790946600000)
sub_df = df[mask]

for _, row in sub_df.iterrows():
    import datetime
    dt = datetime.datetime.fromtimestamp(row["timestamp"]/1000, datetime.timezone.utc)
    print(f"Time: {dt.strftime('%H:%M:%S')} | O:{row['open']} H:{row['high']} L:{row['low']} C:{row['close']} MA3:{row['ma3']:.4f} MA5:{row['ma5']:.4f}")
