"""
test_kc_entry_immutability.py

Regression suite for:
  1. KC_PENDING_INVALIDATED terminal state (100-retry flapping, LONG & SHORT)
  2. Entry Firewall snapshot identity rejection
  3. Multi-symbol collision isolation
  4. Bounded cache (_INVALIDATED_SIGNALS size <= MAX_INVALIDATED_SIGNALS)
  5. Restart deterministic rebuild
"""
import pytest
import pandas as pd

from core.services.kc_pending_entry import (
    evaluate_kc_pending_entry,
    _INVALIDATED_SIGNALS,
    _make_signal_identity,
    MAX_INVALIDATED_SIGNALS,
)
from core.services.entry_firewall import validate_account_entry


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def make_candle(timestamp, open_p, high_p, low_p, close_p, atr, kc_upper, ma5, ma15):
    return {
        'timestamp': timestamp,
        'open': open_p,
        'high': high_p,
        'low': low_p,
        'close': close_p,
        'atr': atr,
        'kc_upper': kc_upper,
        'kc_middle': kc_upper - atr,
        'kc_lower': kc_upper - 2 * atr,
        'ma5': ma5,
        'ma15': ma15,
    }


def long_bars(ts_offset=0):
    """Three-bar LONG breakout setup. Bar3 is RED by default."""
    b1 = make_candle(1000000 + ts_offset, 100, 110, 95, 105, 5, 102, 100, 90)
    b2 = make_candle(1060000 + ts_offset, 105, 115, 104, 112, 5, 103, 103, 92)
    b3_red = make_candle(1120000 + ts_offset, 112, 113, 109, 110, 5, 104, 105, 94)
    b3_green = make_candle(1120000 + ts_offset, 112, 116, 111, 115, 5, 114, 105, 94)
    return b1, b2, b3_red, b3_green


def short_bars(ts_offset=0):
    """Three-bar SHORT breakout setup. Bar3 is GREEN (invalid for SHORT) by default."""
    b1 = make_candle(4000000 + ts_offset, 100, 110, 94, 94, 5, 105, 100, 110)
    b2 = make_candle(4060000 + ts_offset, 95, 100, 85, 87, 5, 98, 97, 108)
    b3_green = make_candle(4120000 + ts_offset, 84, 90, 83, 85, 5, 96, 95, 106)  # invalid for SHORT
    b3_red = make_candle(4120000 + ts_offset, 88, 90, 85, 85, 5, 96, 95, 106)   # valid for SHORT
    return b1, b2, b3_green, b3_red


# ─────────────────────────────────────────────
# 1.  LONG: KC_PENDING_INVALIDATED + 100-retry flapping
# ─────────────────────────────────────────────



# ─────────────────────────────────────────────
# 2.  SHORT mirror
# ─────────────────────────────────────────────



# ─────────────────────────────────────────────
# 3.  Multi-symbol collision isolation
#     BTC Bar3 RED → INVALIDATED; ETH Bar3 GREEN → ENTER  (same timestamps)
# ─────────────────────────────────────────────





# ─────────────────────────────────────────────
# 4.  Bounded cache: len(_INVALIDATED_SIGNALS) <= MAX_INVALIDATED_SIGNALS
# ─────────────────────────────────────────────

def test_bounded_cache_size():
    """
    Insert more than MAX_INVALIDATED_SIGNALS entries.
    The cache must stay within the bound via FIFO eviction.
    """
    _INVALIDATED_SIGNALS.clear()

    # Insert MAX + 500 distinct composite keys
    overflow = MAX_INVALIDATED_SIGNALS + 500
    for i in range(overflow):
        key = _make_signal_identity(f'SYM{i}/USDT', 'LONG', f'sig:{i}:0', i)
        _INVALIDATED_SIGNALS.add(key)

    assert len(_INVALIDATED_SIGNALS) == MAX_INVALIDATED_SIGNALS, (
        f"Cache grew to {len(_INVALIDATED_SIGNALS)}, expected <= {MAX_INVALIDATED_SIGNALS}"
    )

    # The most recently added entries (last 500 from the overflow) must still be present
    for i in range(overflow - 500, overflow):
        key = _make_signal_identity(f'SYM{i}/USDT', 'LONG', f'sig:{i}:0', i)
        assert key in _INVALIDATED_SIGNALS, f"Recent key {key} was evicted prematurely"


# ─────────────────────────────────────────────
# 5.  Restart deterministic rebuild
#     After clearing _INVALIDATED_SIGNALS (simulating restart),
#     re-feeding the same candle history must reproduce KC_PENDING_INVALIDATED.
# ─────────────────────────────────────────────



# ─────────────────────────────────────────────
# 6.  Snapshot identity rejection (entry_firewall.py)
# ─────────────────────────────────────────────

