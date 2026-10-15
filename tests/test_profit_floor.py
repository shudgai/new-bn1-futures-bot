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


















