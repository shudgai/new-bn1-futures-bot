from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2

def test_preflight():
    strategy = PureTrendStrategyV2()
    # Mock data
    bar_prev = {'close': 100, 'kc_upper': 105, 'kc_lower': 95}
    bar_curr = {'open': 106, 'high': 110, 'low': 105, 'close': 108}
    indicators = {'kc_upper': 105, 'kc_lower': 95, 'ma3': 108, 'ma15': 100}
    current_price = 108
    
    # Test 1: 前根未收在軌外 (Prev close < kc_upper for LONG)
    passed, reason = strategy.strict_entry_preflight_check('LONG', bar_prev, bar_curr, current_price, indicators)
    assert not passed and "未實質站上" in reason, f"Test 1 Failed: {reason}"

    # Test 2: 當根收紅開多 (Current price <= open for LONG)
    bar_prev['close'] = 106 # Fix prev close
    current_price = 105
    passed, reason = strategy.strict_entry_preflight_check('LONG', bar_prev, bar_curr, current_price, indicators)
    assert not passed and "紅陰線" in reason, f"Test 2 Failed: {reason}"

    # Test 3: 均線黏合
    current_price = 108
    indicators['ma3'] = 100.01
    passed, reason = strategy.strict_entry_preflight_check('LONG', bar_prev, bar_curr, current_price, indicators)
    assert not passed and "均線黏合" in reason, f"Test 3 Failed: {reason}"
    
    # Test 4: All pass
    indicators['ma3'] = 108
    passed, reason = strategy.strict_entry_preflight_check('LONG', bar_prev, bar_curr, current_price, indicators)
    assert passed, f"Test 4 Failed: {reason}"
    print("All tests passed!")

test_preflight()
