import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy

def create_mock_frame(side="LONG"):
    # Create 5 candles
    rows = []
    base = 100.0
    for i in range(5):
        rows.append({
            'timestamp': 1000000 + i * 60000,
            'open': base, 'high': base + 0.1, 'low': base - 0.1, 'close': base,
            'atr': 1.0,
            'ma3': base,
            'ema_3': base,
            'kc_middle': base,
            'kc_upper': base + 5,
            'kc_lower': base - 5
        })
    return pd.DataFrame(rows)

strategy = DualTrackExitStrategy()

print("--- 測試 1（盤中假穿透測試） ---")
df1 = create_mock_frame()
df1.at[2, 'ma3'] = 101.0
df1.at[3, 'ma3'] = 99.5
df1.at[3, 'open'] = 100.0
df1.at[3, 'close'] = 99.0
df1.at[3, 'high'] = 100.1
df1.at[3, 'low'] = 98.9
df1.at[4, 'high'] = 99.3
pos1 = {"side": "LONG", "entry_price": 100.0, "open_timestamp": 1000000, "price_peak_value": 100.5, "max_price_since_entry": 100.5, "active_stop_price": 98.0}
res1 = strategy.evaluate_exit(pos1, df1, current_price=99.3, is_closed=False)
print(f"結果: {res1}\n")

print("--- 測試 2（收盤確認破線測試） ---")
res2 = strategy.evaluate_exit(pos1, df1, current_price=99.3, is_closed=True)
print(f"結果: {res2}\n")

print("--- 測試 3（真峰谷回撤鎖利測試） ---")
df3 = create_mock_frame()
df3.at[4, 'high'] = 100.8
pos3 = {"side": "LONG", "entry_price": 100.0, "open_timestamp": 1000000, "price_peak_value": 101.2, "max_price_since_entry": 101.2, "profit_anchor_price": 101.2, "active_stop_price": 98.0}
res3 = strategy.evaluate_exit(pos3, df3, current_price=100.75, is_closed=False)
print(f"結果: {res3}\n")
