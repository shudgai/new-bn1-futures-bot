import pandas as pd
from core.services.strategies.unified_entry_strategy import check_streamlined_entry_signal

def test_long_continuation_chasing():
    # Simulate chasing at the high (no pullback touch)
    df = pd.DataFrame([
        {'open': 100, 'close': 102, 'high': 102, 'low': 100, 'kc_upper': 98, 'kc_lower': 90, 'ma3': 95, 'atr': 2},
        {'open': 102, 'close': 105, 'high': 105, 'low': 102, 'kc_upper': 99, 'kc_lower': 91, 'ma3': 96, 'atr': 2},
        {'open': 105, 'close': 106, 'high': 106, 'low': 105, 'kc_upper': 100, 'kc_lower': 92, 'ma3': 97, 'atr': 2},
        {'open': 106, 'close': 107, 'high': 107, 'low': 106, 'kc_upper': 101, 'kc_lower': 93, 'ma3': 98, 'atr': 2},
        # live candle, high up, low is 106, nowhere near kc_upper=102 or ma3=100
        {'open': 107, 'close': 108, 'high': 108, 'low': 106, 'kc_upper': 102, 'kc_lower': 94, 'ma3': 100, 'atr': 2}
    ])
    live_price = 108
    success, reason, action = check_streamlined_entry_signal(df, live_price, "LONG")
    print("Chase Test Success:", success, "Reason:", reason)
    assert not success, "Should not enter when chasing the high without pullback"

def test_long_continuation_pullback():
    # Simulate a valid pullback touch and bounce
    df = pd.DataFrame([
        {'open': 100, 'close': 102, 'high': 102, 'low': 100, 'kc_upper': 98, 'kc_lower': 90, 'ma3': 95, 'atr': 2},
        {'open': 102, 'close': 105, 'high': 105, 'low': 102, 'kc_upper': 99, 'kc_lower': 91, 'ma3': 96, 'atr': 2},
        {'open': 105, 'close': 106, 'high': 106, 'low': 105, 'kc_upper': 100, 'kc_lower': 92, 'ma3': 97, 'atr': 2},
        # Pullback candle: dips to 101 (touching kc_upper=101)
        {'open': 106, 'close': 104, 'high': 106, 'low': 101, 'kc_upper': 101, 'kc_lower': 93, 'ma3': 98, 'atr': 2},
        # live candle: price is 102 (close above ma3=100), distance to kc_upper is 102-102=0 <= 0.4*2 (0.8)
        {'open': 104, 'close': 102.5, 'high': 105, 'low': 102, 'kc_upper': 102, 'kc_lower': 94, 'ma3': 100, 'atr': 2}
    ])
    live_price = 102.5
    success, reason, action = check_streamlined_entry_signal(df, live_price, "LONG")
    print("Pullback Test Success:", success, "Reason:", reason)
    assert success, "Should enter on valid pullback"

if __name__ == '__main__':
    test_long_continuation_chasing()
    test_long_continuation_pullback()
    print("All tests passed!")
