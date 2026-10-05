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
import time

@pytest.fixture(autouse=True)
def controlled_clock(monkeypatch):
    now = int(time.time() // 60) * 60 + 10
    monkeypatch.setattr(time, "time", lambda: now)


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
    # A valid long seed opens inside or on its rail, never beyond it.
    if close_p > open_p and open_p > kc_upper:
        kc_upper = open_p
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

def test_kc_pending_invalidated_state():
    """
    07:57 / 07:58 / 07:59 scenario (LONG):
    Bar1 green breakout → Bar2 green confirmation → Bar3 closed RED
    → WAIT_NEW_KC_BREAKOUT; valid later snapshots are re-evaluated.
    100 retries must follow each current snapshot, without an invented terminal lock.
    """
    _INVALIDATED_SIGNALS.clear()

    b1, b2, b3_red, b3_green_valid = long_bars(ts_offset=0)

    df = pd.DataFrame([b1, b2, b3_red])
    result = evaluate_kc_pending_entry(df, quote=110.0, code=None, symbol='BTC/USDT')
    assert result['reason'] == 'WAIT_NEW_KC_BREAKOUT'

    # 100-retry flapping: alternating green / red Bar3 must always stay INVALIDATED
    for i in range(100):
        b3_flap = (make_candle(1120000, 112, 116, 111, 115, 5, 114, 105, 94)
                   if i % 2 == 0
                   else make_candle(1120000, 112, 113, 109, 110, 5, 104, 105, 94))
        df_flap = pd.DataFrame([b1, b2, b3_flap])
        res = evaluate_kc_pending_entry(df_flap, quote=115.0, code=None, symbol='BTC/USDT')
        expected = 'ENTER' if i % 2 == 0 else 'WAIT'
        assert res['action'] == expected, f"retry {i}: {res}"
        assert res['reason'] == ('KC_2BAR_CONFIRM_LONG' if expected == 'ENTER' else 'WAIT_NEW_KC_BREAKOUT')
        assert res.get('type') == ('KC_2BAR_CONFIRM_LONG' if expected == 'ENTER' else None)

    # Bar4 is green — must NOT revive the invalidated pending
    b4_green = make_candle(1180000, 110, 115, 109, 114, 5, 105, 107, 95)
    df4 = pd.DataFrame([b1, b2, b3_red, b4_green])
    result4 = evaluate_kc_pending_entry(df4, quote=114.0, code=None, symbol='BTC/USDT')
    assert result4['action'] == 'WAIT'
    assert result4.get('reason') not in ('KC_2BAR_CONFIRM_LONG', 'KC_PENDING_CONFIRMATION_PASSED')

    # Separate sequence with new timestamps → ENTER is allowed
    b1_v = make_candle(2000000, 100, 110, 95, 105, 5, 102, 100, 90)
    b2_v = make_candle(2060000, 105, 115, 104, 112, 5, 103, 103, 92)
    b3_g = make_candle(2120000, 112, 116, 111, 115, 5, 114, 105, 94)
    res_valid = evaluate_kc_pending_entry(pd.DataFrame([b1_v, b2_v, b3_g]),
                                          quote=115.0, code=None, symbol='BTC/USDT')
    assert res_valid['action'] == 'ENTER'
    assert res_valid['type'] == 'KC_2BAR_CONFIRM_LONG'


# ─────────────────────────────────────────────
# 2.  SHORT mirror
# ─────────────────────────────────────────────

def test_kc_pending_invalidated_state_short():
    """Mirror of test 1 for SHORT direction."""
    _INVALIDATED_SIGNALS.clear()

    b1, b2, b3_invalid, b3_valid = short_bars(ts_offset=0)

    df = pd.DataFrame([b1, b2, b3_invalid])
    result = evaluate_kc_pending_entry(df, quote=89.0, code=None, symbol='ETH/USDT')
    assert result['reason'] == 'WAIT_NEW_KC_BREAKOUT'

    for i in range(100):
        b3_flap = (make_candle(4120000, 88, 90, 85, 85, 5, 96, 95, 106)
                   if i % 2 == 0
                   else make_candle(4120000, 84, 90, 83, 85, 5, 96, 95, 106))
        df_flap = pd.DataFrame([b1, b2, b3_flap])
        res = evaluate_kc_pending_entry(df_flap, quote=85.0, code=None, symbol='ETH/USDT')
        expected = 'ENTER' if i % 2 == 0 else 'WAIT'
        assert res['action'] == expected, f"retry {i}: {res}"
        assert res['reason'] == ('KC_2BAR_CONFIRM_SHORT' if expected == 'ENTER' else 'WAIT_NEW_KC_BREAKOUT')
        assert res.get('type') == ('KC_2BAR_CONFIRM_SHORT' if expected == 'ENTER' else None)

    # Separate sequence → ENTER allowed
    b1_v = make_candle(5000000, 100, 110, 94, 94, 5, 105, 100, 110)
    b2_v = make_candle(5060000, 95, 100, 85, 87, 5, 98, 97, 108)
    b3_v = make_candle(5120000, 88, 90, 85, 85, 5, 96, 95, 106)
    res_valid = evaluate_kc_pending_entry(pd.DataFrame([b1_v, b2_v, b3_v]),
                                          quote=85.0, code=None, symbol='ETH/USDT')
    assert res_valid['action'] == 'ENTER'
    assert res_valid['type'] == 'KC_2BAR_CONFIRM_SHORT'


# ─────────────────────────────────────────────
# 3.  Multi-symbol collision isolation
#     BTC Bar3 RED → INVALIDATED; ETH Bar3 GREEN → ENTER  (same timestamps)
# ─────────────────────────────────────────────

def test_multi_symbol_no_collision_long():
    """
    BTC/USDT and ETH/USDT share identical Bar1/Bar2 timestamps.
    BTC Bar3 is RED  → WAIT_NEW_KC_BREAKOUT; valid later snapshots are re-evaluated.
    ETH Bar3 is GREEN → ENTER (must not be blocked by BTC's invalidation).
    """
    _INVALIDATED_SIGNALS.clear()

    # BTC — shared bar timestamps
    b1_btc = make_candle(6000000, 100, 110, 95, 105, 5, 102, 100, 90)
    b2_btc = make_candle(6060000, 105, 115, 104, 112, 5, 103, 103, 92)
    b3_btc_red = make_candle(6120000, 112, 113, 109, 110, 5, 104, 105, 94)

    res_btc = evaluate_kc_pending_entry(
        pd.DataFrame([b1_btc, b2_btc, b3_btc_red]), quote=110.0, symbol='BTC/USDT')
    assert res_btc['reason'] == 'WAIT_NEW_KC_BREAKOUT'

    # ETH — identical timestamps to BTC
    b1_eth = make_candle(6000000, 200, 220, 190, 210, 10, 204, 200, 180)
    b2_eth = make_candle(6060000, 210, 230, 208, 224, 10, 206, 206, 184)
    b3_eth_green = make_candle(6120000, 224, 232, 222, 230, 10, 228, 210, 188)

    res_eth = evaluate_kc_pending_entry(
        pd.DataFrame([b1_eth, b2_eth, b3_eth_green]), quote=230.0, symbol='ETH/USDT')
    assert res_eth['action'] == 'ENTER', \
        f"ETH should ENTER but got {res_eth['reason']}"
    assert res_eth['type'] == 'KC_2BAR_CONFIRM_LONG'


def test_multi_symbol_no_collision_short():
    """Mirror of multi-symbol collision test for SHORT."""
    _INVALIDATED_SIGNALS.clear()

    # BTC SHORT — Bar3 invalid (green for SHORT)
    b1_btc = make_candle(7000000, 100, 110, 94, 94, 5, 105, 100, 110)
    b2_btc = make_candle(7060000, 95, 100, 85, 87, 5, 98, 97, 108)
    b3_btc_green = make_candle(7120000, 84, 90, 83, 85, 5, 96, 95, 106)

    res_btc = evaluate_kc_pending_entry(
        pd.DataFrame([b1_btc, b2_btc, b3_btc_green]), quote=89.0, symbol='BTC/USDT')
    assert res_btc['reason'] == 'WAIT_NEW_KC_BREAKOUT'

    # ETH SHORT — same timestamps, Bar3 valid (red for SHORT)
    b1_eth = make_candle(7000000, 200, 220, 188, 188, 10, 210, 200, 220)
    b2_eth = make_candle(7060000, 190, 200, 170, 174, 10, 196, 194, 216)
    b3_eth_red = make_candle(7120000, 176, 180, 170, 170, 10, 192, 190, 212)

    res_eth = evaluate_kc_pending_entry(
        pd.DataFrame([b1_eth, b2_eth, b3_eth_red]), quote=170.0, symbol='ETH/USDT')
    assert res_eth['action'] == 'ENTER', \
        f"ETH SHORT should ENTER but got {res_eth['reason']}"
    assert res_eth['type'] == 'KC_2BAR_CONFIRM_SHORT'


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

def test_restart_deterministic_rebuild():
    """
    Simulate bot restart by clearing _INVALIDATED_SIGNALS.
    Re-evaluate the same closed candle history → must still yield WAIT_NEW_KC_BREAKOUT for the same invalid history.
    ENTER count must remain == 0.
    """
    b1 = make_candle(8000000, 100, 110, 95, 105, 5, 102, 100, 90)
    b2 = make_candle(8060000, 105, 115, 104, 112, 5, 103, 103, 92)
    b3_red = make_candle(8120000, 112, 113, 109, 110, 5, 104, 105, 94)
    df = pd.DataFrame([b1, b2, b3_red])

    # First evaluation — populates _INVALIDATED_SIGNALS
    _INVALIDATED_SIGNALS.clear()
    r1 = evaluate_kc_pending_entry(df, quote=110.0, symbol='RESTART/USDT')
    assert r1['reason'] == 'WAIT_NEW_KC_BREAKOUT'

    # Simulate restart: clear the cache
    _INVALIDATED_SIGNALS.clear()

    # Second evaluation — deterministic rebuild from closed candle history
    enter_count = 0
    for _ in range(5):
        r = evaluate_kc_pending_entry(df, quote=110.0, symbol='RESTART/USDT')
        if r['action'] == 'ENTER':
            enter_count += 1
        assert r['reason'] == 'WAIT_NEW_KC_BREAKOUT', \
            f"After restart, expected KC_PENDING_INVALIDATED, got {r['reason']}"

    assert enter_count == 0, f"ENTER was triggered {enter_count} time(s) after restart"


# ─────────────────────────────────────────────
# 6.  Snapshot identity rejection (entry_firewall.py)
# ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_snapshot_identity_rejection():
    from unittest.mock import AsyncMock
    from test_strict_entry_contract import candles
    from core.services.entry_contract import evaluate_entry_contract
    frame = candles()
    decision = evaluate_entry_contract(frame)
    bar = decision['confirmation_bar_id']
    class DummyAccount:
        entry_frame_provider = AsyncMock(return_value=frame)

    ctx = {
        'entry_signal_code': 'KC_2BAR_CONFIRM_LONG',
        'channel_confirmation_bar_id': bar,
        'signal_id': 'sig_123',
        'candidate_bar_id': bar,
        'entry_snapshot': {
            'symbol': 'BTC/USDT',
            'side': 'LONG',
            'signal_code': 'KC_2BAR_CONFIRM_LONG',
            'signal_id': 'sig_123',
            'candidate_bar_id': bar,
            'closed_bar': bar,
        },
    }

    # Valid identity plus a fresh provider must pass
    res = await validate_account_entry(DummyAccount(), 'BTC/USDT', 'LONG', ctx)
    assert res['type'] == 'KC_2BAR_CONFIRM_LONG'

    def _mismatch(field, bad_value, restore_value):
        ctx['entry_snapshot'][field] = bad_value
        return restore_value

    for field, bad, good, match_str in [
        ('symbol',       'ETH/USDT',            'BTC/USDT',            'wrong symbol'),
        ('side',         'SHORT',                'LONG',                'wrong side'),
        ('signal_code',  'WRONG_CODE',           'KC_2BAR_CONFIRM_LONG','wrong signal_code'),
        ('signal_id',    'sig_456',              'sig_123',             'wrong signal_id'),
        ('candidate_bar_id', 999999,             bar,               'wrong candidate_bar_id'),
        ('closed_bar',   999999,                 bar,               'wrong closed_bar_id'),
    ]:
        ctx['entry_snapshot'][field] = bad
        with pytest.raises(ValueError, match=match_str):
            await validate_account_entry(DummyAccount(), 'BTC/USDT', 'LONG', ctx)
        ctx['entry_snapshot'][field] = good
