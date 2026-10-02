"""
Test KC Strong Breakout Override.
"""
import pandas as pd
from core.services.kc_pending_entry import evaluate_kc_pending_entry, _INVALIDATED_SIGNALS

def build_df(distance, body_sign=1, ma5_slope=1, ma5_ma15_sign=1, waited=1, is_long=True):
    side_sign = 1 if is_long else -1
    atr = 1.0
    kc_upper = 100.0
    kc_lower = 90.0
    kc_edge = kc_upper if is_long else kc_lower
    
    timestamp = 1600000000000
    rows = []
    
    # Bar1: breakout
    b1_open = kc_edge - (side_sign * 0.5)
    b1_close = kc_edge + (side_sign * 0.1)
    b1_ma5 = 95.0
    b1_ma15 = 95.0 - (side_sign * 1.0)
    
    rows.append({'timestamp': timestamp, 'open': b1_open, 'close': b1_close, 'high': max(b1_open, b1_close)+0.1, 'low': min(b1_open, b1_close)-0.1, 'atr': atr, 'kc_upper': kc_upper, 'kc_middle': 95.0, 'kc_lower': kc_lower, 'ma5': b1_ma5, 'ma15': b1_ma15})
    
    # Bar2: confirmation
    timestamp += 60000
    b2_open = b1_close
    b2_close = b2_open + (side_sign * 0.1)
    b2_ma5 = b1_ma5 + (side_sign * 0.1)
    b2_ma15 = b1_ma15
    
    rows.append({'timestamp': timestamp, 'open': b2_open, 'close': b2_close, 'high': max(b2_open, b2_close)+0.1, 'low': min(b2_open, b2_close)-0.1, 'atr': atr, 'kc_upper': kc_upper, 'kc_middle': 95.0, 'kc_lower': kc_lower, 'ma5': b2_ma5, 'ma15': b2_ma15})
    
    # Intervening bars if waited > 1
    prior_ma5 = b2_ma5
    for _ in range(waited - 1):
        timestamp += 60000
        # just inside to wait, body flat
        b_open = kc_edge - (side_sign * 0.1)
        b_close = b_open
        b_ma5 = prior_ma5
        rows.append({'timestamp': timestamp, 'open': b_open, 'close': b_close, 'high': b_open, 'low': b_open, 'atr': atr, 'kc_upper': kc_upper, 'kc_middle': 95.0, 'kc_lower': kc_lower, 'ma5': b_ma5, 'ma15': b2_ma15})
        prior_ma5 = b_ma5

    # Bar3: candidate
    timestamp += 60000
    b3_close = kc_edge + (side_sign * distance)
    b3_open = b3_close - (side_sign * body_sign * 0.1)
    b3_ma5 = prior_ma5 + (side_sign * ma5_slope * 0.1)
    b3_ma15 = b3_ma5 - (side_sign * ma5_ma15_sign * 1.0)
    
    rows.append({'timestamp': timestamp, 'open': b3_open, 'close': b3_close, 'high': max(b3_open, b3_close)+0.1, 'low': min(b3_open, b3_close)-0.1, 'atr': atr, 'kc_upper': kc_upper, 'kc_middle': 95.0, 'kc_lower': kc_lower, 'ma5': b3_ma5, 'ma15': b3_ma15})
    
    return pd.DataFrame(rows)


def test_strong_breakout_override():
    _INVALIDATED_SIGNALS.clear()
    
    # LONG
    # 1. distance=0.4 -> ENTER (normal)
    df = build_df(0.4, is_long=True)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_L1')
    assert res['action'] == 'ENTER'
    assert res['entry_mode'] == 'NORMAL_CONFIRM'
    
    # 2. distance=1.0 -> ENTER (strong breakout)
    df = build_df(1.0, is_long=True)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_L2')
    assert res['action'] == 'ENTER'
    assert res['entry_mode'] == 'STRONG_BREAKOUT_CONFIRM'
    
    # 3. distance=2.0 -> ENTER (strong breakout boundary)
    df = build_df(2.0, is_long=True)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_L3')
    assert res['action'] == 'ENTER'
    assert res['entry_mode'] == 'STRONG_BREAKOUT_CONFIRM'
    
    # 4. distance=2.01 -> CANCELLED_CHASE
    df = build_df(2.01, is_long=True)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_L4')
    assert res['action'] == 'WAIT'
    assert res['reason'] == 'KC_PENDING_CANCELLED_CHASE'
    
    # 5. Bar3 red (body_sign=-1) -> INVALIDATED
    df = build_df(1.0, body_sign=-1, is_long=True)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_L5')
    assert res['action'] == 'WAIT'
    assert res['reason'] == 'KC_PENDING_INVALIDATED'
    
    # 6. MA5 <= MA15 (ma5_ma15_sign=-1) -> CANCELLED
    df = build_df(1.0, ma5_ma15_sign=-1, is_long=True)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_L6')
    assert res['action'] == 'WAIT'
    assert res['reason'] == 'KC_PENDING_CANCELLED_MA_STRUCTURE'
    
    # 7. MA5 slope <= 0 (ma5_slope=-1) -> CANCELLED
    df = build_df(1.0, ma5_slope=-1, is_long=True)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_L7')
    assert res['action'] == 'WAIT'
    assert res['reason'] == 'KC_PENDING_CANCELLED_MA_SLOPE'
    
    # SHORT
    _INVALIDATED_SIGNALS.clear()
    
    # 1. distance=0.4 -> ENTER (normal)
    df = build_df(0.4, is_long=False)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_S1')
    assert res['action'] == 'ENTER'
    assert res['entry_mode'] == 'NORMAL_CONFIRM'
    
    # 2. distance=1.0 -> ENTER (strong breakout)
    df = build_df(1.0, is_long=False)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_S2')
    assert res['action'] == 'ENTER'
    assert res['entry_mode'] == 'STRONG_BREAKOUT_CONFIRM'
    
    # 3. distance=2.0 -> ENTER (strong breakout boundary)
    df = build_df(2.0, is_long=False)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_S3')
    assert res['action'] == 'ENTER'
    
    # 4. distance=2.01 -> CANCELLED_CHASE
    df = build_df(2.01, is_long=False)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_S4')
    assert res['action'] == 'WAIT'
    assert res['reason'] == 'KC_PENDING_CANCELLED_CHASE'
    
    # 5. Bar3 green (body_sign=-1) -> INVALIDATED
    df = build_df(1.0, body_sign=-1, is_long=False)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_S5')
    assert res['action'] == 'WAIT'
    assert res['reason'] == 'KC_PENDING_INVALIDATED'
    
    # 6. MA5 >= MA15 -> CANCELLED
    df = build_df(1.0, ma5_ma15_sign=-1, is_long=False)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_S6')
    assert res['action'] == 'WAIT'
    assert res['reason'] == 'KC_PENDING_CANCELLED_MA_STRUCTURE'
    
    # 7. MA5 slope >= 0 -> CANCELLED
    df = build_df(1.0, ma5_slope=-1, is_long=False)
    res = evaluate_kc_pending_entry(df, quote=100.0, symbol='TEST_S7')
    assert res['action'] == 'WAIT'
    assert res['reason'] == 'KC_PENDING_CANCELLED_MA_SLOPE'



def test_lobster_pepe_fixture():
    _INVALIDATED_SIGNALS.clear()
    
    # PEPE 00:59 to 01:01
    rows = [
        {'timestamp': 1, 'open': 0.004427, 'close': 0.004414, 'high': 0.004427, 'low': 0.004414, 'atr': 0.000005, 'kc_upper': 0.004450, 'kc_middle': 0.004430, 'kc_lower': 0.004419, 'ma5': 0.004414, 'ma15': 0.004421},
        {'timestamp': 60001, 'open': 0.004414, 'close': 0.004413, 'high': 0.004414, 'low': 0.004413, 'atr': 0.000005, 'kc_upper': 0.004450, 'kc_middle': 0.004430, 'kc_lower': 0.004417, 'ma5': 0.004413, 'ma15': 0.004420},
        {'timestamp': 120001, 'open': 0.004413, 'close': 0.004405, 'high': 0.004413, 'low': 0.004405, 'atr': 0.000005, 'kc_upper': 0.004450, 'kc_middle': 0.004430, 'kc_lower': 0.004415, 'ma5': 0.004411, 'ma15': 0.004419}
    ]
    df = pd.DataFrame(rows)
    res = evaluate_kc_pending_entry(df, quote=0.004405, symbol='1000PEPE/USDT')
    
    assert res['action'] == 'ENTER'
    assert res['type'] == 'KC_3BAR_CONFIRM_SHORT'
    assert res['entry_mode'] == 'STRONG_BREAKOUT_CONFIRM'
    assert res['kc_distance_atr'] == (0.004415 - 0.004405) / 0.000005
