import pandas as pd
from core.services.strategies.unified_entry_strategy import check_streamlined_entry_signal

# Test SHORT inside KC Rejection
df_short_inside = pd.DataFrame([
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_lower': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_lower': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_lower': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_lower': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_lower': 98, 'atr': 5},
    # Previous bar (inside KC)
    {'open': 100, 'high': 100, 'low': 95, 'close': 96, 'kc_lower': 95, 'kc_upper': 105, 'atr': 5},
    # Trigger bar (inside KC)
    {'open': 96, 'high': 96, 'low': 93, 'close': 94, 'kc_lower': 93, 'kc_upper': 105, 'atr': 5},
    # Live bar
    {'open': 94, 'high': 94, 'low': 90, 'close': 92, 'kc_lower': 92, 'kc_upper': 105, 'atr': 5},
])

ok, reason, _ = check_streamlined_entry_signal(df_short_inside, "SHORT", 92, "NO_POSITION")
assert not ok
assert "BLOCKED_STRICT_BREAKOUT" in reason
print("✅ Test Passed: Short entry blocked due to strict breakout rule (inside KC)")

# Test LONG bullish requirement
df_long_bearish = pd.DataFrame([
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_upper': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_upper': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_upper': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_upper': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_upper': 98, 'atr': 5},
    # Previous bar (outside KC upper)
    {'open': 100, 'high': 110, 'low': 95, 'close': 108, 'kc_lower': 95, 'kc_upper': 105, 'atr': 5},
    # Trigger bar (outside KC upper, but bearish)
    {'open': 109, 'high': 115, 'low': 105, 'close': 106, 'kc_lower': 95, 'kc_upper': 105, 'atr': 5},
    # Live bar
    {'open': 106, 'high': 110, 'low': 105, 'close': 107, 'kc_lower': 95, 'kc_upper': 105, 'atr': 5},
])

ok, reason, _ = check_streamlined_entry_signal(df_long_bearish, "LONG", 107, "NO_POSITION")
assert not ok
assert "BLOCKED_STRICT_BREAKOUT" in reason
print("✅ Test Passed: Long entry blocked due to current bar being bearish despite being outside")

