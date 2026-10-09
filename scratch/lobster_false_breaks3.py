import pandas as pd
import sys, os
sys.path.append(os.getcwd())
from core.strategy import SuperTrendKeltnerStrategy

try:
    df = pd.read_csv("scratch/lobster_real_1m_history.csv")
    strat = SuperTrendKeltnerStrategy()
    df = strat.compute_indicators(df)

    target_ts = 1790965020000.0
    matches = df[df['timestamp'] == target_ts]
    if len(matches) == 0:
        print(f"ERROR: timestamp {target_ts} not found in df!")
        sys.exit(1)
        
    target_idx = matches.index[0]

    for i in range(target_idx - 10, target_idx + 3):
        row = df.iloc[i]
        print(f"K: {row['timestamp']} O:{row['open']} H:{row['high']} L:{row['low']} C:{row['close']} KCU:{row['kc_upper']} KCM:{row['kc_middle']} KCL:{row['kc_lower']} MA3:{row['ma3']} MA15:{row['ma15']} ATR:{row['atr']}")

    sub = df.iloc[target_idx - 30 : target_idx + 30].copy()

    low_breaks = sub[sub['low'] < sub['kc_lower']]
    close_breaks = sub[sub['close'] < sub['kc_lower']]
    bearish_close_breaks = sub[(sub['close'] < sub['kc_lower']) & (sub['close'] < sub['open'])]

    print(f"FALSE_BREAK_COUNT_LOW = {len(low_breaks)}")
    print(f"FALSE_BREAK_COUNT_CLOSE = {len(close_breaks)}")
    print(f"FALSE_BREAK_COUNT_BEARISH_CLOSE = {len(bearish_close_breaks)}")
except Exception as e:
    import traceback
    traceback.print_exc()
