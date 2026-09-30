import pandas as pd
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2

def test_filters():
    strategy = PureTrendStrategyV2()
    # Mock data
    closed_data = pd.DataFrame({
        'high': [100, 110, 120, 110, 100],
        'low': [90, 80, 70, 80, 90],
        'close': [95, 85, 75, 85, 95], # prev2 will be index 3, prev1 will be index 4
        'kc_upper': [105]*5,
        'kc_lower': [95]*5
    })
    
    # prev2 is at index 3: close=85, kc_lower=95 (outside band!)
    bar_prev = {'close': 95, 'open': 100, 'kc_upper': 105, 'kc_lower': 100, 'atr': 10}
    bar_curr = {'open': 95, 'high': 100, 'low': 90, 'close': 92, 'timestamp': 100000}
    bar_prev['timestamp'] = 40000
    
    indicators = {'ma3': 95, 'ma15': 100}
    
    res = strategy.check_standard_example_breakout_entry(
        bar_prev, bar_curr, 92, indicators, closed_data
    )
    assert res is None, "Should be blocked due to stale breakout (prev2 already outside)"
    
    # Fix stale breakout
    closed_data.loc[3, 'close'] = 98 # inside kc_lower (95)
    
    # Now check floor short blocked
    # swing_low should be at index 2 (70)
    # current_price = 72, swing_low = 70, atr = 10 -> diff is 2 < 15
    res = strategy.check_standard_example_breakout_entry(
        bar_prev, bar_curr, 72, indicators, closed_data
    )
    assert res is None, "Should be blocked due to floor short (close to swing low)"
    
    print("Filters successfully tested and working.")

test_filters()
