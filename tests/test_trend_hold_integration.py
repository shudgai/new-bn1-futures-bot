import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
from core.services.exits.peak_trailing_exit import HARD_REASON, ABNORMAL_REASON

def get_base_snapshot(status_target):
    # Returns a snapshot dict tailored for evaluating to HOLD, WARNING, or RELEASED for LONG
    # Base is open_timestamp = 60.0, price near 100
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
    if status_target == 'HOLD':
        # Default is HOLD (ma5 > ma15, ma5_slope > 0, ma15_slope >= 0)
        # Price > ma5 (105 > 100)
        pass
    elif status_target == 'WARNING':
        # Price < ma5 but > kc_middle
        snapshot['ma5'] = 110 # Price 105 < ma5 110
        snapshot['last_ma5'] = 109 # Keep slope positive
    elif status_target == 'RELEASED':
        # MA5 < MA15
        snapshot['ma5'] = 80
        snapshot['last_ma5'] = 82
        snapshot['ma15'] = 90
    return snapshot

def test_long_hold_peak_trailing():
    # 1. HOLD + Peak trailing -> no exit
    position = {'side': 'LONG', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 110, 'peak_net_pnl': 10.0, 'identity': ['LONG', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 90, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot('HOLD')
    res = evaluate_peak_trailing(position, 105, snapshot)
    assert res is None
    assert position.get('trend_hold_status') == 'HOLD'

def test_long_warning_peak_trailing():
    # 2. WARNING + Peak trailing -> no exit
    position = {'side': 'LONG', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 110, 'peak_net_pnl': 10.0, 'identity': ['LONG', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 90, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot('WARNING')
    res = evaluate_peak_trailing(position, 105, snapshot)
    assert res is None
    assert position.get('trend_hold_status') == 'WARNING'

def test_long_warning_doji():
    # 4. WARNING + Doji -> no exit
    position = {'side': 'LONG', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 106, 'peak_net_pnl': 0.0, 'identity': ['LONG', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 90, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot('WARNING')
    # Make last bar a doji (body / span <= 0.15)
    snapshot.update({'last_open': 105, 'last_close': 105.1, 'last_high': 106, 'last_low': 104})
    # Price dropping below open and low
    res = evaluate_peak_trailing(position, 103, snapshot)
    assert res is None
    assert position.get('trend_hold_status') == 'WARNING'

def test_long_released_peak_trailing():
    # 5. RELEASED + Peak trailing -> normal exit
    position = {'side': 'LONG', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 110, 'peak_net_pnl': 10.0, 'identity': ['LONG', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 90, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot('RELEASED')
    res = evaluate_peak_trailing(position, 105, snapshot)
    assert res is not None
    assert res['trigger'] == 'TRAILING_2U_LADDER'
    assert position.get('trend_hold_status') == 'RELEASED'

def test_long_warning_waterfall():
    # 7. WARNING + Waterfall -> MUST exit
    position = {'side': 'LONG', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 106, 'peak_net_pnl': 0.0, 'identity': ['LONG', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 90, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot('WARNING')
    # Drop from 105 live_open to 95 (> 1.5 ATR = 3)
    res = evaluate_peak_trailing(position, 95, snapshot)
    assert res is not None
    assert res['trigger'] == 'WATERFALL_DROP'
    assert position.get('trend_hold_status') == 'WARNING'

def test_long_warning_hard_stop():
    # 8. WARNING + Hard stop -> MUST exit
    position = {'side': 'LONG', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 106, 'peak_net_pnl': 0.0, 'identity': ['LONG', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 102, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot('WARNING')
    res = evaluate_peak_trailing(position, 101, snapshot) # price < initial_sl (102)
    assert res is not None
    assert res['trigger'] == 'INITIAL_ATR'
    assert position.get('trend_hold_status') == 'WARNING'

def get_base_snapshot_short(status_target):
    snapshot = {
        'quote_ms': 60000,
        'live_bar_ms': 60000,
        'closed_bar_ms': 0,
        'live_open': 95,
        'atr': 2.0,
        'ma5': 100,
        'ma15': 110,
        'kc_middle': 120,
        'last_open': 96,
        'last_high': 97,
        'last_low': 94,
        'last_close': 95,
        'last_ma5': 101,
        'last_ma15': 110,
        'last_kc_middle': 120
    }
    if status_target == 'HOLD':
        pass
    elif status_target == 'WARNING':
        snapshot['ma5'] = 90
        snapshot['last_ma5'] = 91
    elif status_target == 'RELEASED':
        snapshot['ma5'] = 120
        snapshot['last_ma5'] = 118
        snapshot['ma15'] = 110
    return snapshot

def test_short_hold_peak_trailing():
    position = {'side': 'SHORT', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 90, 'peak_net_pnl': 10.0, 'identity': ['SHORT', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 110, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot_short('HOLD')
    res = evaluate_peak_trailing(position, 95, snapshot)
    assert res is None
    assert position.get('trend_hold_status') == 'HOLD'

def test_short_warning_peak_trailing():
    position = {'side': 'SHORT', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 90, 'peak_net_pnl': 10.0, 'identity': ['SHORT', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 110, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot_short('WARNING')
    res = evaluate_peak_trailing(position, 95, snapshot)
    assert res is None
    assert position.get('trend_hold_status') == 'WARNING'

def test_short_warning_doji():
    position = {'side': 'SHORT', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 94, 'peak_net_pnl': 0.0, 'identity': ['SHORT', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 110, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot_short('WARNING')
    snapshot.update({'last_open': 95, 'last_close': 95.1, 'last_high': 96, 'last_low': 94})
    res = evaluate_peak_trailing(position, 97, snapshot)
    assert res is None
    assert position.get('trend_hold_status') == 'WARNING'

def test_short_released_peak_trailing():
    position = {'side': 'SHORT', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 90, 'peak_net_pnl': 10.0, 'identity': ['SHORT', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 110, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot_short('RELEASED')
    res = evaluate_peak_trailing(position, 95, snapshot)
    assert res is not None
    assert res['trigger'] == 'TRAILING_2U_LADDER'
    assert position.get('trend_hold_status') == 'RELEASED'

def test_short_warning_flash_crash():
    position = {'side': 'SHORT', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 94, 'peak_net_pnl': 0.0, 'identity': ['SHORT', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 110, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot_short('WARNING')
    res = evaluate_peak_trailing(position, 105, snapshot) # price surges > 1.5 ATR
    assert res is not None
    assert res['trigger'] == 'WATERFALL_DROP'
    
def test_short_warning_hard_stop():
    position = {'side': 'SHORT', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 94, 'peak_net_pnl': 0.0, 'identity': ['SHORT', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 103, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot_short('WARNING')
    res = evaluate_peak_trailing(position, 104, snapshot) 
    assert res is not None
    assert res['trigger'] == 'INITIAL_ATR'
    
def test_trend_hold_transition_hold_warning_hold():
    position = {'side': 'LONG', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 110, 'peak_net_pnl': 10.0, 'identity': ['LONG', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 90, 'open_timestamp': 60.0}
    
    # HOLD
    snap1 = get_base_snapshot('HOLD')
    res1 = evaluate_peak_trailing(position, 105, snap1)
    assert res1 is None
    assert position.get('trend_hold_status') == 'HOLD'
    assert position['peak_trailing_state']['peak_price'] == 110
    
    # WARNING
    snap2 = get_base_snapshot('WARNING')
    snap2['quote_ms'] = 61000
    res2 = evaluate_peak_trailing(position, 105, snap2)
    assert res2 is None
    assert position.get('trend_hold_status') == 'WARNING'
    
    # HOLD again
    snap3 = get_base_snapshot('HOLD')
    snap3['quote_ms'] = 62000
    res3 = evaluate_peak_trailing(position, 106, snap3)
    assert res3 is None
    assert position.get('trend_hold_status') == 'HOLD'
    # Check state integrity
    assert position['peak_trailing_state']['peak_price'] == 110
    assert 'identity' in position['peak_trailing_state']

def test_trend_hold_transition_warning_released():
    position = {'side': 'LONG', 'entry_price': 100, 'qty': 1, 'peak_trailing_state': {'peak_price': 110, 'peak_net_pnl': 10.0, 'identity': ['LONG', 60.0, 100.0, 1.0], 'policy': 'abnormal_body_only_v2'}, 'initial_sl': 90, 'open_timestamp': 60.0}
    
    # WARNING
    snap2 = get_base_snapshot('WARNING')
    res2 = evaluate_peak_trailing(position, 105, snap2)
    assert res2 is None
    assert position.get('trend_hold_status') == 'WARNING'
    
    # RELEASED
    snap3 = get_base_snapshot('RELEASED')
    snap3['quote_ms'] = 61000
    res3 = evaluate_peak_trailing(position, 105, snap3)
    assert res3 is not None
    assert res3['trigger'] == 'TRAILING_2U_LADDER'
    assert position.get('trend_hold_status') == 'RELEASED'
