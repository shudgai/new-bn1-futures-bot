"""Tests for Catastrophic Profit Floor in peak_trailing_exit.py."""
import pytest
import copy
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing, STATE_KEY

def get_position(side='LONG', entry_price=100.0, qty=1.0, entry_atr=2.0, leverage=1.0):
    pos = {
        'side': side,
        'open_timestamp': 60,
        'entry_price': entry_price,
        'qty': qty,
        'leverage': leverage,
        'margin': entry_price * qty / leverage,
        'entry_atr': entry_atr,
        STATE_KEY: {}
    }
    return pos

def observe(p, price, snap=61000, atr=1.0):
    return evaluate_peak_trailing(p, price, snap, atr, fee=0.0, slippage=0.0)

def enable_pf(monkeypatch, arm=2.0, lock=1.0):
    monkeypatch.setattr('core.services.exits.peak_trailing_exit.PROFIT_FLOOR_ENABLED', True)
    monkeypatch.setattr('core.services.exits.peak_trailing_exit.PROFIT_FLOOR_ARM_ATR', arm)
    monkeypatch.setattr('core.services.exits.peak_trailing_exit.PROFIT_FLOOR_LOCK_ATR', lock)

def test_1_default_disabled_existing_behavior_unchanged(monkeypatch):
    p = get_position('LONG', 100, 1, 2)
    # Disabled by default
    # 4 ATR run => price 108
    assert observe(p, 108) is None
    # No profit floor armed state
    assert not p[STATE_KEY].get('profit_floor_armed', False)

def test_2_invalid_config_fail_closed(monkeypatch):
    p = get_position('LONG', 100, 1, 2)
    # Lock > Arm is invalid
    enable_pf(monkeypatch, arm=2.0, lock=3.0)
    assert observe(p, 108) is None
    assert not p[STATE_KEY].get('profit_floor_armed', False)
    
    # Arm <= 0 is invalid
    enable_pf(monkeypatch, arm=-1.0, lock=-2.0)
    assert observe(p, 108) is None
    assert not p[STATE_KEY].get('profit_floor_armed', False)

def test_3_immutable_entry_atr_not_live(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('LONG', 100, 1, 2.0) # entry atr = 2.0
    # Live ATR is 0.5. If it used live ATR, (102 - 100) / 0.5 = 4 ATR -> would arm.
    # But it must use entry ATR (2.0), so (102 - 100) / 2.0 = 1 ATR -> NOT armed.
    assert observe(p, 102, atr=0.5) is None
    assert not p[STATE_KEY].get('profit_floor_armed', False)

def test_4_long_arm_inclusive(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('LONG', 100, 1, 2.0)
    assert observe(p, 103.9) is None
    assert not p[STATE_KEY].get('profit_floor_armed', False)
    
    assert observe(p, 104.0) is None # EXACTLY 2.0 ATR -> 100 + 4
    assert p[STATE_KEY].get('profit_floor_armed', True)

def test_5_short_arm_inclusive(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('SHORT', 100, 1, 2.0)
    assert observe(p, 96.1) is None
    assert not p[STATE_KEY].get('profit_floor_armed', False)
    
    assert observe(p, 96.0) is None
    assert p[STATE_KEY].get('profit_floor_armed', True)

def test_6_long_floor_trigger(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('LONG', 100, 1, 2.0)
    assert observe(p, 104) is None
    assert p[STATE_KEY]['profit_floor_price'] == 102.0
    
    # Drop to 102.1 -> no trigger
    assert observe(p, 102.1) is None
    # Drop to 102.0 -> trigger
    res = observe(p, 102.0)
    assert res['trigger'] == 'EXIT_CATASTROPHIC_PROFIT_FLOOR'

def test_7_short_floor_trigger(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('SHORT', 100, 1, 2.0)
    assert observe(p, 96) is None
    assert p[STATE_KEY]['profit_floor_price'] == 98.0
    
    res = observe(p, 98.0)
    assert res['trigger'] == 'EXIT_CATASTROPHIC_PROFIT_FLOOR'

def test_8_hold_bypass(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('HOLD', 'TEST'))
    p = get_position('LONG', 100, 1, 2.0)
    assert observe(p, 104) is None
    res = observe(p, 102.0)
    assert res['trigger'] == 'EXIT_CATASTROPHIC_PROFIT_FLOOR'

def test_9_warning_bypass(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('WARNING', 'TEST'))
    p = get_position('LONG', 100, 1, 2.0)
    assert observe(p, 104) is None
    res = observe(p, 102.0)
    assert res['trigger'] == 'EXIT_CATASTROPHIC_PROFIT_FLOOR'

def test_10_unknown_bypass(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', lambda *a, **k: ('UNKNOWN', 'TEST'))
    p = get_position('LONG', 100, 1, 2.0)
    assert observe(p, 104) is None
    res = observe(p, 102.0)
    assert res['trigger'] == 'EXIT_CATASTROPHIC_PROFIT_FLOOR'

def test_11_armed_never_disarms(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('LONG', 100, 1, 2.0)
    assert observe(p, 104) is None
    assert p[STATE_KEY]['profit_floor_armed']
    
    # Drop below arm but above lock
    assert observe(p, 103) is None
    assert p[STATE_KEY]['profit_floor_armed']

def test_12_long_floor_monotonic(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('LONG', 100, 1, 2.0)
    assert observe(p, 104) is None
    assert p[STATE_KEY]['profit_floor_price'] == 102.0
    
    # Config change tries to loosen floor to 0.5 ATR (101.0)
    enable_pf(monkeypatch, arm=2.0, lock=0.5)
    assert observe(p, 105) is None
    # 105 - 100 = 5.0 -> would set floor to 101.0, but existing is 102.0 -> remains 102.0 because max(102.0, 101.0)
    assert p[STATE_KEY]['profit_floor_price'] == 102.0

def test_13_short_floor_monotonic(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('SHORT', 100, 1, 2.0)
    assert observe(p, 96) is None
    assert p[STATE_KEY]['profit_floor_price'] == 98.0
    
    enable_pf(monkeypatch, arm=2.0, lock=0.5)
    assert observe(p, 95) is None
    # min(98.0, 100 - 1.0) -> 98.0 vs 99.0 -> 98.0
    assert p[STATE_KEY]['profit_floor_price'] == 98.0

def test_14_independent_from_2u_ladder(monkeypatch):
    enable_pf(monkeypatch, arm=4.0, lock=2.0)
    # ladder arms at 4U net, locks at 2U net.
    p = get_position('LONG', 100, 100, 2.0) # margin=10000. 1 pt = 100U
    
    # price to 100.03 -> net 3U -> neither armed
    assert observe(p, 100.03) is None
    
    # price to 100.04 -> net 4U -> ladder armed (locks 2U), floor NOT armed (needs 4 ATR = 108 price)
    assert observe(p, 100.04) is None
    assert p[STATE_KEY]['peak_net_pnl'] >= 4.0
    assert not p[STATE_KEY].get('profit_floor_armed', False)
    
    # price to 108 -> net 800U -> floor armed
    assert observe(p, 108.0) is None
    assert p[STATE_KEY]['profit_floor_armed']

def test_15_initial_atr_stop_priority(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('LONG', 100, 1, 2.0)
    p['initial_sl'] = 90.0
    assert observe(p, 104) is None
    # price drops to 89, triggers both initial sl and profit floor
    res = observe(p, 89.0)
    # initial sl has higher priority
    assert res['trigger'] == 'INITIAL_ATR'

def test_16_waterfall_priority(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('LONG', 100, 1, 2.0)
    assert observe(p, 104) is None
    # Waterfall config: 1.5 ATR = 3.0. price drops from 103 (open) to 99.0
    snap = dict(quote_ms=61000, live_open=103.0, atr=2.0, live_bar_ms=60000, closed_bar_ms=0)
    res = observe(p, 99.0, snap=snap)
    assert res['trigger'] == 'WATERFALL_DROP'

def test_17_restart_persistence(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('LONG', 100, 1, 2.0)
    assert observe(p, 104) is None
    assert p[STATE_KEY]['profit_floor_armed']
    
    p2 = copy.deepcopy(p)
    # Restart identical position, state persists
    assert observe(p2, 103) is None
    assert p2[STATE_KEY]['profit_floor_armed']

def test_18_new_position_reset(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('LONG', 100, 1, 2.0)
    assert observe(p, 104) is None
    
    # New position (different time)
    p2 = get_position('LONG', 100, 1, 2.0)
    p2['open_timestamp'] = 120
    p2[STATE_KEY] = p[STATE_KEY] # carry over state incorrectly
    assert observe(p2, 103, snap=121000) is None
    # should be reset
    assert not p2[STATE_KEY].get('profit_floor_armed', False)

def test_19_reversal_reset(monkeypatch):
    enable_pf(monkeypatch, arm=2.0, lock=1.0)
    p = get_position('LONG', 100, 1, 2.0)
    assert observe(p, 104) is None
    
    p2 = get_position('SHORT', 100, 1, 2.0)
    p2[STATE_KEY] = p[STATE_KEY]
    assert observe(p2, 99) is None
    assert not p2[STATE_KEY].get('profit_floor_armed', False)
