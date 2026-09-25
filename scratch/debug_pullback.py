import pandas as pd
from core.services.strategies.unified_entry_strategy import check_streamlined_entry_signal
df = pd.DataFrame([
    {'open': 100, 'close': 102, 'high': 102, 'low': 100, 'kc_upper': 98, 'kc_lower': 90, 'ma3': 95, 'atr': 2},
    {'open': 102, 'close': 105, 'high': 105, 'low': 102, 'kc_upper': 99, 'kc_lower': 91, 'ma3': 96, 'atr': 2},
    {'open': 105, 'close': 106, 'high': 106, 'low': 105, 'kc_upper': 100, 'kc_lower': 92, 'ma3': 97, 'atr': 2},
    {'open': 106, 'close': 104, 'high': 106, 'low': 101, 'kc_upper': 101, 'kc_lower': 93, 'ma3': 98, 'atr': 2},
    {'open': 104, 'close': 102.5, 'high': 105, 'low': 102, 'kc_upper': 102, 'kc_lower': 94, 'ma3': 100, 'atr': 2}
])
live_price = 102.5
success, reason, _ = check_streamlined_entry_signal(df, "LONG", live_price)
print("Result:", success, reason)
