import pandas as pd
import numpy as np

# Load data
df = pd.read_csv("scratch/lobster_real_1m_history.csv")
ts = df['timestamp'].values
idx = np.where(ts == 1790965020000)[0][0]
print(f"Timestamp 1790965020000 at index {idx}")
