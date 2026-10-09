import pytest
import pandas as pd
from core.services.canonical_entry import (
    evaluate_two_closed_kc_breakout, 
    CanonicalExecutionStateMachine
)

def create_bar(ts, o, h, l, c, kcu, kcl, ma5=10, ma15=5, atr=1, closed=True):
    return {
        'timestamp': float(ts),
        'open': float(o),
        'high': float(h),
        'low': float(l),
        'close': float(c),
        'kc_upper': float(kcu),
        'kc_lower': float(kcl),
        'ma5': float(ma5),
        'ma15': float(ma15),
        'atr': float(atr),
        'is_closed': closed
    }

def test_19_same_canonical_signal_cannot_open_twice():
    # TEST 19 is equivalent to dedup test
    sm = CanonicalExecutionStateMachine()
    # Register K2
    k2_ts = 70000
    sig1 = sm.register_signal("LOBSTER", "LONG", k2_ts, "TWO_CLOSED_KC_BREAKOUT_LONG")
    
    # Consume it
    sm.consume_signal("LOBSTER", "LONG")
    
    # Live K3 attempts to evaluate
    k3 = create_bar(130000, 10, 20, 8, 14, 9.5, 9, closed=False)
    action = sm.evaluate_live_candle("LOBSTER", "LONG", k3)
    
    # Should be WAIT because it was consumed
    assert action == 'WAIT'
    assert sig1.state == 'CONSUMED'

def test_20_successful_open_consumes_signal():
    sm = CanonicalExecutionStateMachine()
    sig1 = sm.register_signal("LOBSTER", "LONG", 70000, "TWO_CLOSED_KC_BREAKOUT_LONG")
    
    k3 = create_bar(130000, 10, 20, 8, 14, 9.5, 9, closed=False)
    action1 = sm.evaluate_live_candle("LOBSTER", "LONG", k3)
    assert action1 == 'READY'
    
    # Simulate execution successful -> consume
    sm.consume_signal("LOBSTER", "LONG")
    
    # Subsequent evaluation returns WAIT
    action2 = sm.evaluate_live_candle("LOBSTER", "LONG", k3)
    assert action2 == 'WAIT'

def test_21_successful_close_cannot_resurrect_old_signal():
    # If a signal was consumed, and we slide window, it won't resurrect
    # because register_signal checks k2_timestamp
    sm = CanonicalExecutionStateMachine()
    k2_ts = 70000
    sig1 = sm.register_signal("LOBSTER", "LONG", k2_ts, "TWO_CLOSED_KC_BREAKOUT_LONG")
    sm.consume_signal("LOBSTER", "LONG")
    
    # Engine loop runs again, tries to register the SAME signal
    sig2 = sm.register_signal("LOBSTER", "LONG", k2_ts, "TWO_CLOSED_KC_BREAKOUT_LONG")
    
    assert sig1 is sig2
    assert sig2.state == 'CONSUMED'

def test_22_reentry_requires_new_k1_k2_confirmation():
    # In canonical flow, reentry means registering a new signal with phase='POST_EXIT_REENTRY'
    # The new signal must have a NEW k2_timestamp to be active
    sm = CanonicalExecutionStateMachine()
    k2_ts_old = 70000
    sig1 = sm.register_signal("LOBSTER", "LONG", k2_ts_old, "TWO_CLOSED_KC_BREAKOUT_LONG")
    sm.consume_signal("LOBSTER", "LONG")
    
    # Attempt reentry with OLD K2 -> blocked
    sig2 = sm.register_signal("LOBSTER", "LONG", k2_ts_old, "TWO_CLOSED_KC_BREAKOUT_LONG", phase="POST_EXIT_REENTRY")
    assert sig2.state == 'CONSUMED'
    
    # Attempt reentry with NEW K2 -> allowed
    k2_ts_new = 130000
    sig3 = sm.register_signal("LOBSTER", "LONG", k2_ts_new, "TWO_CLOSED_KC_BREAKOUT_LONG", phase="POST_EXIT_REENTRY")
    assert sig3.state == 'NEW'

def test_23_long_state_cannot_leak_short():
    sm = CanonicalExecutionStateMachine()
    sig_long = sm.register_signal("LOBSTER", "LONG", 70000, "TWO_CLOSED_KC_BREAKOUT_LONG")
    sig_short = sm.register_signal("LOBSTER", "SHORT", 70000, "TWO_CLOSED_KC_BREAKOUT_SHORT")
    
    sm.consume_signal("LOBSTER", "LONG")
    assert sig_long.state == 'CONSUMED'
    assert sig_short.state == 'NEW'  # Short is completely unaffected by Long's mutation

def test_24_short_state_cannot_leak_long():
    sm = CanonicalExecutionStateMachine()
    sig_long = sm.register_signal("LOBSTER", "LONG", 70000, "TWO_CLOSED_KC_BREAKOUT_LONG")
    sig_short = sm.register_signal("LOBSTER", "SHORT", 70000, "TWO_CLOSED_KC_BREAKOUT_SHORT")
    
    sm.consume_signal("LOBSTER", "SHORT")
    assert sig_short.state == 'CONSUMED'
    assert sig_long.state == 'NEW' 

def test_25_lobster_state_cannot_leak_other_symbol():
    sm = CanonicalExecutionStateMachine()
    sig_lobster = sm.register_signal("LOBSTER", "LONG", 70000, "TWO_CLOSED_KC_BREAKOUT_LONG")
    sig_crab = sm.register_signal("CRAB", "LONG", 70000, "TWO_CLOSED_KC_BREAKOUT_LONG")
    
    sm.consume_signal("LOBSTER", "LONG")
    assert sig_lobster.state == 'CONSUMED'
    assert sig_crab.state == 'NEW'


# ---- Canonical state identity (Phase 1A pre-cutover gate) ----

def _live(ts, o, c, h=None, l=None, closed=False):
    h = max(o, c) if h is None else h
    l = min(o, c) if l is None else l
    return create_bar(ts, o, h, l, c, 9.5, 9, closed=closed)


def test_identity_k5_cannot_reuse_old_signal_when_closes_missed():
    sm = CanonicalExecutionStateMachine()
    sm.register_signal("LOBSTER", "LONG", 70000, "TWO_CLOSED_KC_BREAKOUT_LONG")
    # K3/K4 close never observed; K5 live bar (K2 + 3 intervals) is strong same-direction
    assert sm.evaluate_live_candle("LOBSTER", "LONG", _live(250000, 10, 14)) == 'EXPIRE'
    assert sm.active_signals[("LOBSTER", "LONG")].state == 'EXPIRED'


def test_identity_k5_after_wait_k4_cannot_reuse_old_signal():
    sm = CanonicalExecutionStateMachine()
    sm.register_signal("LOBSTER", "LONG", 70000, "TWO_CLOSED_KC_BREAKOUT_LONG")
    assert sm.evaluate_live_candle("LOBSTER", "LONG", _live(130000, 10, 10.05, 11, 9, closed=True)) == 'WAIT'
    assert sm.active_signals[("LOBSTER", "LONG")].state == 'WAIT_K4'
    assert sm.evaluate_live_candle("LOBSTER", "LONG", _live(250000, 10, 14)) == 'EXPIRE'


def test_identity_k4_timestamp_while_new_fails_closed():
    sm = CanonicalExecutionStateMachine()
    sm.register_signal("LOBSTER", "LONG", 70000, "TWO_CLOSED_KC_BREAKOUT_LONG")
    # K3 close missed -> cannot know it was a Doji -> no K4 extension
    assert sm.evaluate_live_candle("LOBSTER", "LONG", _live(190000, 10, 14)) == 'EXPIRE'


def test_identity_older_k2_cannot_replace_newer():
    sm = CanonicalExecutionStateMachine()
    new = sm.register_signal("LOBSTER", "LONG", 130000, "TWO_CLOSED_KC_BREAKOUT_LONG")
    assert sm.register_signal("LOBSTER", "LONG", 70000, "TWO_CLOSED_KC_BREAKOUT_LONG") is None
    assert sm.active_signals[("LOBSTER", "LONG")] is new


def test_identity_consume_and_evaluate_reject_mismatched_k2():
    sm = CanonicalExecutionStateMachine()
    sig = sm.register_signal("LOBSTER", "LONG", 130000, "TWO_CLOSED_KC_BREAKOUT_LONG")
    assert sm.consume_signal("LOBSTER", "LONG", k2_timestamp=70000) is False
    assert sig.state == 'NEW'
    assert sm.evaluate_live_candle("LOBSTER", "LONG", _live(190000, 10, 14), k2_timestamp=70000) == 'WAIT'
    assert sm.evaluate_live_candle("LOBSTER", "LONG", _live(190000, 10, 14), k2_timestamp=130000) == 'READY'


def test_identity_short_mirror_k5_expires():
    sm = CanonicalExecutionStateMachine()
    sm.register_signal("LOBSTER", "SHORT", 70000, "TWO_CLOSED_KC_BREAKOUT_SHORT")
    assert sm.evaluate_live_candle("LOBSTER", "SHORT", _live(250000, 14, 10)) == 'EXPIRE'
    assert ("LOBSTER", "LONG") not in sm.active_signals


# ---- Canonical formation K1/K2 adjacency (Phase 1A preflight) ----
from core.services.canonical_entry import CANONICAL_ENTRY_INTERVAL_MS as _IV


def _pair(side, k1_ts, k2_ts):
    if side == 'LONG':  # strong green bodies closing above kc_upper=10
        return (create_bar(k1_ts, 10, 12, 9.9, 11.8, 10, 8),
                create_bar(k2_ts, 11.8, 14, 11.7, 13.8, 10.5, 8))
    return (create_bar(k1_ts, 10, 10.1, 8, 8.2, 12, 10),
            create_bar(k2_ts, 8.2, 8.3, 6, 6.2, 12, 9.5))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_adjacency_adjacent_pair_forms_signal(side):
    k1, k2 = _pair(side, 1_000_000, 1_000_000 + _IV)
    assert evaluate_two_closed_kc_breakout(k1, k2, side) == f'TWO_CLOSED_KC_BREAKOUT_{side}'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('delta', [2 * _IV, 6 * _IV, -_IV, 0], ids=['gap_one', 'gap_multiple', 'reversed', 'same_ts'])
def test_adjacency_non_adjacent_pair_no_signal(side, delta):
    k1, k2 = _pair(side, 1_000_000, 1_000_000 + delta)
    assert evaluate_two_closed_kc_breakout(k1, k2, side) == 'NO_SIGNAL'


def test_adjacency_missing_timestamp_no_signal():
    k1, k2 = _pair('LONG', 1_000_000, 1_000_000 + _IV)
    del k1['timestamp']
    assert evaluate_two_closed_kc_breakout(k1, k2, 'LONG') == 'NO_SIGNAL'

def test_26_canonical_pipeline_call_order():
    pytest.skip("EXPECTED BLOCKED: Runtime orchestration not yet bound to Canonical Owner (Phase 1 cutover needed).")

def test_27_kc_legacy_bypass():
    pytest.skip("EXPECTED BLOCKED: Runtime orchestration not yet bound to Canonical Owner (Phase 1 cutover needed).")

def test_28_second_bar_legacy_bypass():
    pytest.skip("EXPECTED BLOCKED: Runtime orchestration not yet bound to Canonical Owner (Phase 1 cutover needed).")

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
