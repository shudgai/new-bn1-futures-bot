import pandas as pd

df = pd.read_csv('scratch/lobster_real_1m_history.csv')
# Look around 1790988420000.0 to 1790988600000.0
subset = df[(df['timestamp'] >= 1790988300000) & (df['timestamp'] <= 1790988600000)]
for i, row in subset.iterrows():
    print(f"Bar: {row['timestamp']} Low: {row['low']} Close: {row['close']} MA3: {row['ma3']} MA5: {row['ma5']} MA15: {row['ma15']} KC_MID: {row['kc_middle']}")
