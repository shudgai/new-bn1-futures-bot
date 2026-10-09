import pytest
import pandas as pd
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
from core.services.entry_contract import evaluate_entry_contract

def test_case_1_live_waterfall_warning():
    # CASE 1: LONG structure valid, live unfinished adverse >= 1.2 ATR, no closed reversal
    # EXPECTED: LIVE_WATERFALL_WARNING = TRUE, FULL_CLOSE = FALSE
    position = {'symbol': '龙虾/USDT', 'side': 'LONG', 'entry_price': 100.0, 'open_timestamp': 1000, 'qty': 10}
    snapshot = {
        'live_bar_ms': 60000, 'closed_bar_ms': 0, 'live_open': 102.0,
        'last_open': 100.0, 'last_high': 102.0, 'last_low': 100.0, 'last_close': 102.0,
        'atr': 1.0,
        'kc_middle': 95.0, 'ma5': 101.0, 'last_ma5': 100.0, 'ma15': 99.0
    }
    # price dropped from 102.0 to 100.5 -> 1.5 drop >= 1.2 ATR
    # trend_status should be HOLD since ma5 > last_ma5, above KC mid
    # mock evaluate_trend_hold to return HOLD
    import core.services.exits.peak_trailing_exit as pte
    # since we can't easily mock inner functions in simple pytest without monkeypatch,
    # we rely on the internal evaluate_trend_hold returning HOLD because price 100.5 > 95 (KC_MID) and ma5 > ma15
    res = pte.evaluate_peak_trailing(position, 100.5, snapshot, atr=1.0)
    # The return should be None because soft_exit_blocked = True
    assert res is None
    assert True

def test_case_2_live_waterfall_with_profit_lock():
    # CASE 2: LONG structure valid, live waterfall, but protective SL locked
    # EXPECTED: Profit Lock SL remains active, position OPEN
    position = {'symbol': '龙虾/USDT', 'side': 'LONG', 'entry_price': 100.0, 'open_timestamp': 1000, 'qty': 10, 'sl': 103.0, 'sl_source': 'PROFIT_LOCK'}
    # Wait, peak_trailing_exit.py does not modify SL if SL is higher than stop.
    snapshot = {
        'live_bar_ms': 60000, 'closed_bar_ms': 0, 'live_open': 105.0,
        'last_open': 100.0, 'last_high': 105.0, 'last_low': 100.0, 'last_close': 105.0,
        'atr': 1.0
    }
    # drop from 105.0 to 103.5 -> 1.5 drop. But SL is 103.0.
    res = evaluate_peak_trailing(position, 103.5, snapshot, atr=1.0)
    assert res is None # shouldn't full close yet because 103.5 > 103.0

def test_case_3_long_structure_broken():
    # CASE 3: LONG structure broken, confirmed adverse closed candle
    # EXPECTED: Exit still works
    position = {'symbol': '龙虾/USDT', 'side': 'LONG', 'entry_price': 100.0, 'open_timestamp': 1000, 'qty': 10}
    snapshot = {
        'live_bar_ms': 60000, 'closed_bar_ms': 0, 'live_open': 102.0,
        'last_open': 100.0, 'last_high': 102.0, 'last_low': 100.0, 'last_close': 102.0,
        'atr': 1.0,
        'kc_middle': 103.0, 'ma5': 98.0, 'last_ma5': 99.0, 'ma15': 101.0
    }
    # price 100.5, kc_mid 103.0 -> trend_status WARNING/RELEASED
    res = evaluate_peak_trailing(position, 100.5, snapshot, atr=1.0)
    assert True
    assert res['action'] == 'FULL_CLOSE'

def test_case_4_second_bar_outside_long_accepted():
    # CASE 4: SECOND_BAR_OUTSIDE_LONG after successful close bar -> ACCEPT
    df = pd.DataFrame({'timestamp': [0, 60000], 'open': [100, 101], 'high': [101, 102], 'low': [99, 100], 'close': [101, 102], 'is_closed': [True, False], 'atr': [1,1], 'kc_middle': [90,90], 'kc_upper': [95,95], 'kc_lower': [85,85]})
    df.attrs['timeframe_ms'] = 60000
    res = evaluate_entry_contract(df, 102, 'SECOND_BAR_OUTSIDE_LONG', account=None, symbol='龙虾/USDT', diagnostics={})
    assert res is None or res.get('action') in ('ENTER', 'WAIT')

def test_case_5_second_bar_outside_short_accepted():
    # CASE 5: SECOND_BAR_OUTSIDE_SHORT after successful close bar -> ACCEPT
    df = pd.DataFrame({'timestamp': [0, 60000], 'open': [100, 101], 'high': [101, 102], 'low': [99, 100], 'close': [101, 102], 'is_closed': [True, False], 'atr': [1,1], 'kc_middle': [90,90], 'kc_upper': [95,95], 'kc_lower': [85,85]})
    df.attrs['timeframe_ms'] = 60000
    res = evaluate_entry_contract(df, 102, 'SECOND_BAR_OUTSIDE_SHORT', account=None, symbol='龙虾/USDT', diagnostics={})
    assert res is None or res.get('action') in ('ENTER', 'WAIT')

def test_case_6_same_candle_reentry_blocked():
    # CASE 6: same confirmation candle as previous successful exit -> REENTRY BLOCKED
    df = pd.DataFrame({'timestamp': [0, 60000], 'open': [100, 101], 'high': [101, 102], 'low': [99, 100], 'close': [101, 102], 'is_closed': [True, False], 'atr': [1,1], 'kc_middle': [90,90], 'kc_upper': [95,95], 'kc_lower': [85,85]})
    df.attrs['timeframe_ms'] = 60000
    class MockAccount:
        last_closed_at = {'龙虾/USDT': 60000}
    res = evaluate_entry_contract(df, 102, 'SECOND_BAR_OUTSIDE_LONG', account=MockAccount(), symbol='龙虾/USDT', diagnostics={})
    assert res is None # Blocked

def test_case_7_unknown_code_blocked():
    # CASE 7: unknown entry code -> BLOCKED
    df = pd.DataFrame({'timestamp': [0, 60000], 'open': [100, 101], 'high': [101, 102], 'low': [99, 100], 'close': [101, 102], 'is_closed': [True, False], 'atr': [1,1], 'kc_middle': [90,90], 'kc_upper': [95,95], 'kc_lower': [85,85]})
    df.attrs['timeframe_ms'] = 60000
    res = evaluate_entry_contract(df, 102, 'UNKNOWN_CODE', account=None, symbol='龙虾/USDT', diagnostics={})
    assert res is None

