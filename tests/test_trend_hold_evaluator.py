import pytest
from core.services.exits.trend_hold_evaluator import evaluate_trend_hold

def test_long_strong_trend_bounces_ma5():
    # 1. LONG 強趨勢 + 回踩 MA5 → 不平倉
    # MA5 > MA15, MA5 slope > 0, MA15 slope >= 0, price > kc_mid, price < MA5
    position = {'side': 'LONG'}
    snapshot = {
        'ma5': 100, 'last_ma5': 99,
        'ma15': 90, 'last_ma15': 90,
        'kc_middle': 80,
        'last_close': 105
    }
    status, reason = evaluate_trend_hold(position, snapshot, 98) # price < ma5 but > kc_mid
    assert status == 'WARNING'
    assert reason == 'PRICE_BELOW_MA5_BUT_KC_HELD'

def test_long_strong_trend_red_candle():
    # 2. LONG 強趨勢 + 單根紅K → 不平倉
    # Price is still > MA5, just a red candle (which shouldn't break TREND_HOLD)
    position = {'side': 'LONG'}
    snapshot = {
        'ma5': 100, 'last_ma5': 99,
        'ma15': 90, 'last_ma15': 90,
        'kc_middle': 80,
        'last_close': 105
    }
    status, reason = evaluate_trend_hold(position, snapshot, 102)
    assert status == 'HOLD'
    assert reason == 'STRONG_TREND_LONG'

def test_long_trend_hold_doji():
    # 3. LONG 強趨勢 + Doji → 不平倉
    position = {'side': 'LONG'}
    snapshot = {
        'ma5': 100, 'last_ma5': 99,
        'ma15': 90, 'last_ma15': 90,
        'kc_middle': 80,
        'last_close': 105
    }
    status, reason = evaluate_trend_hold(position, snapshot, 101)
    assert status == 'HOLD'

def test_long_trend_hold_peak_retracement():
    # 4. LONG 強趨勢 + 一般 Peak 回吐 → 不平倉
    position = {'side': 'LONG'}
    snapshot = {
        'ma5': 100, 'last_ma5': 99,
        'ma15': 90, 'last_ma15': 90,
        'kc_middle': 80,
        'last_close': 105
    }
    status, reason = evaluate_trend_hold(position, snapshot, 102)
    assert status == 'HOLD'

def test_long_price_below_ma5_but_above_kc():
    # 5. LONG 跌破 MA5但仍守 KC 中軌 → WARNING，不平
    position = {'side': 'LONG'}
    snapshot = {
        'ma5': 100, 'last_ma5': 99,
        'ma15': 90, 'last_ma15': 90,
        'kc_middle': 80,
        'last_close': 105
    }
    status, reason = evaluate_trend_hold(position, snapshot, 95)
    assert status == 'WARNING'

def test_long_close_below_kc_and_ma5_flat():
    # 6. LONG 跌破 KC 中軌 + MA5轉弱 → RELEASE
    position = {'side': 'LONG'}
    snapshot = {
        'ma5': 100, 'last_ma5': 100, # slope = 0
        'ma15': 90, 'last_ma15': 90,
        'kc_middle': 105,
        'last_close': 102 # closed below kc_mid
    }
    status, reason = evaluate_trend_hold(position, snapshot, 102)
    assert status == 'RELEASED'
    assert reason == 'CLOSED_BELOW_KC_MID_AND_MA5_WEAK'

def test_long_ma5_crosses_below_ma15():
    # 7. MA5 < MA15 → RELEASE
    position = {'side': 'LONG'}
    snapshot = {
        'ma5': 89, 'last_ma5': 92,
        'ma15': 90, 'last_ma15': 90,
        'kc_middle': 80,
        'last_close': 95
    }
    status, reason = evaluate_trend_hold(position, snapshot, 95)
    assert status == 'RELEASED'
    assert reason == 'MA5_BELOW_MA15'

def test_short_strong_trend():
    # 10. SHORT 做完全鏡像測試
    position = {'side': 'SHORT'}
    snapshot = {
        'ma5': 90, 'last_ma5': 91,
        'ma15': 100, 'last_ma15': 100,
        'kc_middle': 110,
        'last_close': 85
    }
    status, reason = evaluate_trend_hold(position, snapshot, 88)
    assert status == 'HOLD'
    assert reason == 'STRONG_TREND_SHORT'

    status, reason = evaluate_trend_hold(position, snapshot, 95) # > ma5 but < kc_mid
    assert status == 'WARNING'
    assert reason == 'PRICE_ABOVE_MA5_BUT_KC_HELD'

    snapshot['ma5'] = 101 # crosses ma15
    status, reason = evaluate_trend_hold(position, snapshot, 102)
    assert status == 'RELEASED'
    assert reason == 'MA5_ABOVE_MA15'
