import pandas as pd
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2

def test_filters():
    strategy = PureTrendStrategyV2()
    # Mock data: 6 bars
    closed_data = pd.DataFrame({
        'high': [100, 110, 120, 110, 100, 100],
        'low': [90, 80, 70, 80, 90, 90],
        'close': [95, 85, 75, 85, 95, 95], # -6 to -1 (prev1 is at -1)
        'kc_upper': [105]*6,
        'kc_lower': [95]*6
    })
    # Make index -2 (prev2), -3 (prev3) OUTSIDE kc_lower
    closed_data.loc[4, 'close'] = 90  # prev2 outside (90 < 95)
    closed_data.loc[3, 'close'] = 90  # prev3 outside (90 < 95)
    
    bar_prev = {'close': 95, 'open': 100, 'kc_upper': 105, 'kc_lower': 100, 'atr': 10, 'timestamp': 40000}
    bar_curr = {'open': 95, 'high': 100, 'low': 90, 'close': 92, 'timestamp': 100000}
    
    indicators = {'ma3': 95, 'ma15': 100}
    
    res = strategy.check_standard_example_breakout_entry(
        bar_prev, bar_curr, 92, indicators, closed_data
    )
    assert res is None, "Should be blocked due to stale breakout (only 2 bars inside)"
    
    # Fix stale breakout (put them inside)
    closed_data.loc[4, 'close'] = 100 
    closed_data.loc[3, 'close'] = 100 
    
    # Now check floor short blocked
    # lowest_15 will be 70
    # current_price = 72, lowest_15 = 70, atr = 10 -> diff is 2 < 20 (2.0 ATR)
    res = strategy.check_standard_example_breakout_entry(
        bar_prev, bar_curr, 72, indicators, closed_data
    )
    assert res is None, "Should be blocked due to floor short (close to rolling low)"
    
    print("Filters successfully tested and working.")

test_filters()
