import pandas as pd
from core.services.strategies.unified_entry_strategy import check_pullback_continuation

df = pd.DataFrame([
    # Older trend down
    {'open': 105, 'high': 105, 'low': 98, 'close': 99, 'kc_lower': 100, 'ema_20': 110, 'kc_middle': 110, 'atr': 5},
    # Pullback starts (touches lower band)
    {'open': 99, 'high': 101, 'low': 98, 'close': 100, 'kc_lower': 100, 'ema_20': 109, 'kc_middle': 109, 'atr': 5}, # -4
    {'open': 100, 'high': 103, 'low': 100, 'close': 102, 'kc_lower': 100, 'ema_20': 108, 'kc_middle': 108, 'atr': 5}, # -3
    # Resumption 
    {'open': 102, 'high': 102, 'low': 96, 'close': 97, 'kc_lower': 100, 'ema_20': 107, 'kc_middle': 107, 'atr': 5}, # -2 (Trigger)
    {'open': 97, 'high': 97, 'low': 95, 'close': 96, 'kc_lower': 99, 'ema_20': 106, 'kc_middle': 106, 'atr': 5}, # -1 (Live)
])
df['kc_middle'] = df['ema_20']

ok, msg = check_pullback_continuation(df, "SHORT")
assert ok is True
print("Test passed: Short Pullback Continuation successfully triggered!")
print(msg)
