import pandas as pd
from core.services.strategies.unified_entry_strategy import check_pullback_continuation

rows = []
for i in range(10):
    rows.append({'open': 105, 'high': 105, 'low': 98, 'close': 99, 'kc_lower': 100, 'ema_20': 110, 'kc_middle': 110, 'atr': 5})

rows.extend([
    # Pullback starts (touches lower band)
    {'open': 99, 'high': 101, 'low': 98, 'close': 100, 'kc_lower': 100, 'ema_20': 109, 'kc_middle': 109, 'atr': 5},
    {'open': 100, 'high': 103, 'low': 100, 'close': 102, 'kc_lower': 100, 'ema_20': 108, 'kc_middle': 108, 'atr': 5},
    # Resumption 
    {'open': 102, 'high': 102, 'low': 96, 'close': 97, 'kc_lower': 100, 'ema_20': 107, 'kc_middle': 107, 'atr': 5},
    {'open': 97, 'high': 97, 'low': 95, 'close': 96, 'kc_lower': 99, 'ema_20': 106, 'kc_middle': 106, 'atr': 5},
])
df = pd.DataFrame(rows)

ok, msg = check_pullback_continuation(df, "SHORT")
if not ok:
    print("FAILED:", msg)
assert ok is True
print("Test passed: Short Pullback Continuation successfully triggered!")
print(msg)
