import pandas as pd

df = pd.read_csv("scratch/lobster_real_1m_history.csv")
# 10/04 07:50:08 (UTC+8) is 10/03 23:50:08 UTC
mask = (df["timestamp"] >= 1791071400000 - (60000 * 5)) & (df["timestamp"] <= 1791072079000 + (60000 * 5))
sub_df = df[mask]

for _, row in sub_df.iterrows():
    import datetime
    dt = datetime.datetime.fromtimestamp(row["timestamp"]/1000, datetime.timezone.utc)
    print(f"Time: {dt.strftime('%H:%M:%S')} | O:{row['open']} H:{row['high']} L:{row['low']} C:{row['close']}")
