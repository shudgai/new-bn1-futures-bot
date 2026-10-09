import pandas as pd
df = pd.read_csv("scratch/lobster_real_1m_history.csv")
# Calculate indicators manually if not present, but wait, they aren't in the CSV! The CSV just has O/H/L/C.
# But wait, we can just use the core engine to compute indicators on the dataframe!
import sys, os
sys.path.append(os.getcwd())
from core.strategy import SuperTrendKeltnerStrategy
strat = SuperTrendKeltnerStrategy()
df = strat.compute_indicators(df)

# Look at 30 bars before and after K1 (1790965020000.0)
target_ts = 1790965020000.0
target_idx = df[df['timestamp'] == target_ts].index[0]
sub = df.iloc[target_idx - 30 : target_idx + 30].copy()

low_breaks = sub[sub['low'] < sub['kc_lower']]
close_breaks = sub[sub['close'] < sub['kc_lower']]
bearish_close_breaks = sub[(sub['close'] < sub['kc_lower']) & (sub['close'] < sub['open'])]

print(f"FALSE_BREAK_COUNT_LOW = {len(low_breaks)}")
print(f"FALSE_BREAK_COUNT_CLOSE = {len(close_breaks)}")
print(f"FALSE_BREAK_COUNT_BEARISH_CLOSE = {len(bearish_close_breaks)}")
