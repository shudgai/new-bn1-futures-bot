import pandas as pd

df = pd.read_csv("scratch/lobster_real_1m_history.csv")
# find bars >= 1790941860000
mask = df["timestamp"] >= 1790941860000
sub_df = df[mask].head(25)

for _, row in sub_df.iterrows():
    print(f"Time: {int(row['timestamp'])} | O:{row['open']} H:{row['high']} L:{row['low']} C:{row['close']} V:{row['volume']}")
