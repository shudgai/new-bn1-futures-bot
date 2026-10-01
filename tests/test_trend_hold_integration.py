import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing

def test_waterfall_not_blocked():
    position = {
        'side': 'LONG',
        'entry_price': 100,
        'qty': 1,
        'state': {}, 'open_timestamp': 60.0
    }
    # Waterfall drop: current price drops > 1.5 ATR from live_open
    # live_open = 100, prior_atr = 2.0. Drop to 95 (5 points = 2.5 ATR > 1.5 ATR)
    snapshot = {
        'quote_ms': 60000,
        'live_bar_ms': 60000,
        'closed_bar_ms': 0,
        'live_open': 100,
        'atr': 2.0,
        'ma5': 100,
        'ma15': 90,
        'kc_middle': 80,
        'last_open': 100,
        'last_high': 102,
        'last_low': 98,
        'last_close': 101,
        'last_ma5': 99,
        'last_ma15': 90,
        'last_kc_middle': 80
    }
    
    # Evaluate at price 95 (waterfall drop)
    res = evaluate_peak_trailing(position, 95, snapshot)
    assert res is not None
    assert res['action'] == 'FULL_CLOSE'
    assert res['reason'] == 'EXIT_ADVERSE_ABNORMAL_BODY'
    assert res['trigger'] == 'WATERFALL_DROP'
    # Check that trend hold was evaluated
    assert position.get('trend_hold_status') in ('WARNING', 'RELEASED')

def test_soft_exit_blocked():
    position = {
        'side': 'LONG',
        'entry_price': 100,
        'qty': 1,
        'state': {
            'peak_price': 110,
            'peak_net_pnl': 10.0,
            'identity': ['LONG', 60.0, 100.0, 1.0]
        },
        'initial_sl': 90, 'open_timestamp': 60.0
    }
    # We want trailing ladder to be hit (net pnl drops below locked_net).
    # Locked net for 10.0 peak is floor((10-4)/2)*2 + 2 = 8.0.
    # Current net pnl at price 105 is around 5.0 (ignoring fees).
    # This should trigger TRAILING_2U_LADDER.
    
    snapshot = {
        'quote_ms': 60000,
        'live_bar_ms': 60000,
        'closed_bar_ms': 0,
        'live_open': 105,
        'atr': 2.0,
        'ma5': 100,
        'ma15': 90,
        'kc_middle': 80,
        'last_open': 104,
        'last_high': 106,
        'last_low': 103,
        'last_close': 105,
        'last_ma5': 99,
        'last_ma15': 90,
        'last_kc_middle': 80
    }
    
    # Evaluate at price 105
    res = evaluate_peak_trailing(position, 105, snapshot)
    
    # Because TREND_HOLD is HOLD (ma5=100 > kc=80, ma5 slope > 0, price 105 > ma5)
    # The trailing ladder should be blocked (or relaxed).
    # Relaxed locked_net is 2.0. At price 105, gain is 5.0, which is > 2.0.
    # So NO exit!
    assert res is None
    assert position.get('trend_hold_status') == 'HOLD'

