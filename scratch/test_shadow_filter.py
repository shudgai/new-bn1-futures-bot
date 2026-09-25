import pandas as pd
from core.services.strategies.unified_entry_strategy import check_streamlined_entry_signal

# Test SHORT Shadow Rejection
df_short = pd.DataFrame([
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_lower': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_lower': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_lower': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_lower': 98, 'atr': 5},
    {'open': 100, 'high': 101, 'low': 95, 'close': 97, 'kc_lower': 98, 'atr': 5},
    # Previous bar
    {'open': 100, 'high': 100, 'low': 90, 'close': 90, 'kc_lower': 95, 'atr': 5},
    # Trigger bar (current_candle): body=2, lower shadow=4 (4/2 > 0.5)
    {'open': 90, 'high': 90, 'low': 84, 'close': 88, 'kc_lower': 90, 'atr': 5},
    # Live bar
    {'open': 88, 'high': 90, 'low': 88, 'close': 89, 'kc_lower': 89, 'atr': 5},
])

ok, reason, _ = check_streamlined_entry_signal(df_short, "SHORT", 89, "NO_POSITION")
assert not ok
assert "BLOCKED_SHADOW_REJECTION" in reason
print("✅ Test Passed: Short entry blocked due to long lower shadow")

# Test Exhaustion Rejection
df_exhaust = pd.DataFrame([
    {'open': 100, 'high': 110, 'low': 95, 'close': 105, 'kc_middle': 100, 'atr': 5},
    {'open': 100, 'high': 110, 'low': 95, 'close': 105, 'kc_middle': 100, 'atr': 5},
    # Rebound bar crossing kc_middle
    {'open': 95, 'high': 102, 'low': 90, 'close': 101, 'kc_middle': 100, 'atr': 5},
    {'open': 101, 'high': 101, 'low': 95, 'close': 95, 'kc_middle': 100, 'atr': 5},
    {'open': 95, 'high': 95, 'low': 90, 'close': 90, 'kc_middle': 98, 'atr': 5},
    # Previous bar
    {'open': 90, 'high': 90, 'low': 85, 'close': 85, 'kc_lower': 90, 'kc_middle': 95, 'atr': 5},
    # Trigger bar (current_candle): body=4 (4 < 5.0) -> Rebound recently, needs 1.0 ATR
    {'open': 85, 'high': 85, 'low': 80, 'close': 81, 'kc_lower': 85, 'atr': 5},
    # Live bar
    {'open': 81, 'high': 81, 'low': 79, 'close': 79, 'kc_lower': 80, 'atr': 5},
])

ok, reason, _ = check_streamlined_entry_signal(df_exhaust, "SHORT", 79, "NO_POSITION")
assert not ok
assert "BLOCKED_EXHAUSTION" in reason
print("✅ Test Passed: Short entry blocked due to recent rebound and insufficient momentum")

