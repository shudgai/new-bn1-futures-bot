"""Model T Ratcheting Profit Floor tests — ARMED_CONFIG_POLICY = FREEZE.

Covers:
  LONG tests (L1-L8) and SHORT mirror (S1-S8)
  Freeze policy tests (global change must not affect armed position)
  JSON round-trip restart persistence (LONG + SHORT)
  Monotonic floor after restart
  Hard stop regression
  Waterfall regression
"""
import copy
import json
import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing, STATE_KEY


def make_position(side='LONG', entry_price=100.0, qty=1.0, entry_atr=2.0, open_ms=60):
    return {
        'side': side,
        'open_timestamp': open_ms,
        'entry_price': entry_price,
        'qty': qty,
        'leverage': 1.0,
        'margin': entry_price * qty,
        'entry_atr': entry_atr,
        STATE_KEY: {}
    }


def observe(pos, price, snap=61000):
    return evaluate_peak_trailing(pos, price, snap, fee=0.0, slippage=0.0)


def enable_model_t(monkeypatch, arm, trail):
    monkeypatch.setattr('core.services.exits.peak_trailing_exit.PROFIT_FLOOR_ENABLED', True)
    monkeypatch.setattr('core.services.exits.peak_trailing_exit.LOCK_ARM_ATR', arm)
    monkeypatch.setattr('core.services.exits.peak_trailing_exit.TRAILING_DISTANCE_ATR', trail)


def json_round_trip(pos):
    """Simulate save → reload by JSON-serialising and deserialising position state."""
    serialised = json.dumps(pos[STATE_KEY])
    pos2 = make_position(
        side=pos['side'],
        entry_price=pos['entry_price'],
        qty=pos['qty'],
        entry_atr=pos['entry_atr'],
        open_ms=pos['open_timestamp'],
    )
    pos2[STATE_KEY] = json.loads(serialised)
    return pos2


# ==================================================
# FAIL-CLOSED: parameters None → no floor activity
# ==================================================

def test_fail_closed_when_parameters_none():
    pos = make_position('LONG', 100, entry_atr=2.0)
    assert observe(pos, 108) is None
    assert not pos[STATE_KEY].get('profit_floor_armed', False)


def test_fail_closed_when_disabled(monkeypatch):
    monkeypatch.setattr('core.services.exits.peak_trailing_exit.PROFIT_FLOOR_ENABLED', False)
    monkeypatch.setattr('core.services.exits.peak_trailing_exit.LOCK_ARM_ATR', 0.5)
    monkeypatch.setattr('core.services.exits.peak_trailing_exit.TRAILING_DISTANCE_ATR', 0.25)
    pos = make_position('LONG', 100, entry_atr=2.0)
    assert observe(pos, 108) is None
    assert not pos[STATE_KEY].get('profit_floor_armed', False)


# ==================================================
# LONG TESTS
# ==================================================

class TestModelTLong:

    def test_L1_pre_arm_no_floor(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        assert observe(pos, 101.9) is None
        assert not pos[STATE_KEY].get('profit_floor_armed', False)
        assert 'profit_floor_price' not in pos[STATE_KEY]

    def test_L2_arm_threshold_reached(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        assert observe(pos, 102.0) is None
        assert pos[STATE_KEY].get('profit_floor_armed') is True
        assert pos[STATE_KEY].get('frozen_trailing_distance_atr') == pytest.approx(0.25)
        assert pos[STATE_KEY].get('frozen_lock_arm_atr') == pytest.approx(1.0)
        assert pos[STATE_KEY]['profit_floor_price'] == pytest.approx(102.0 - 0.25 * 2.0)

    def test_L3_first_peak_sets_floor(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        observe(pos, 102.0)
        assert pos[STATE_KEY]['profit_floor_price'] == pytest.approx(102.0 - 0.25 * 2.0)

    def test_L4_higher_peak_raises_floor(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        observe(pos, 102.0)
        floor_after_first = pos[STATE_KEY]['profit_floor_price']
        observe(pos, 104.0)
        assert pos[STATE_KEY]['profit_floor_price'] == pytest.approx(104.0 - 0.25 * 2.0)
        assert pos[STATE_KEY]['profit_floor_price'] > floor_after_first

    def test_L5_pullback_above_floor_hold(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        observe(pos, 104.0)
        floor = pos[STATE_KEY]['profit_floor_price']
        assert observe(pos, floor + 0.01) is None

    def test_L6_floor_hit_triggers_close(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        observe(pos, 104.0)
        floor = pos[STATE_KEY]['profit_floor_price']
        result = observe(pos, floor)
        assert result is not None
        assert result['trigger'] == 'EXIT_PROFIT_LOCK_FLOOR'

    def test_L7_floor_never_decreases(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        observe(pos, 104.0)
        high_floor = pos[STATE_KEY]['profit_floor_price']
        observe(pos, 103.6)
        assert pos[STATE_KEY]['profit_floor_price'] == pytest.approx(high_floor)

    def test_L8_persistence_in_memory(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        observe(pos, 104.0)
        saved_state = copy.deepcopy(pos[STATE_KEY])
        pos2 = make_position('LONG', 100, entry_atr=2.0)
        pos2[STATE_KEY] = saved_state
        assert pos2[STATE_KEY].get('profit_floor_armed') is True
        assert pos2[STATE_KEY]['profit_floor_price'] == pytest.approx(saved_state['profit_floor_price'])


# ==================================================
# SHORT MIRROR TESTS
# ==================================================

class TestModelTShort:

    def test_S1_pre_arm_no_floor(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        assert observe(pos, 98.1) is None
        assert not pos[STATE_KEY].get('profit_floor_armed', False)

    def test_S2_arm_threshold_reached(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        assert observe(pos, 98.0) is None
        assert pos[STATE_KEY].get('profit_floor_armed') is True
        assert pos[STATE_KEY].get('frozen_trailing_distance_atr') == pytest.approx(0.25)
        assert pos[STATE_KEY].get('frozen_lock_arm_atr') == pytest.approx(1.0)
        assert pos[STATE_KEY]['profit_floor_price'] == pytest.approx(98.0 + 0.25 * 2.0)

    def test_S3_favorable_low_sets_floor(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        observe(pos, 98.0)
        assert pos[STATE_KEY]['profit_floor_price'] == pytest.approx(98.0 + 0.25 * 2.0)

    def test_S4_lower_favorable_low_decreases_floor(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        observe(pos, 98.0)
        floor_after_first = pos[STATE_KEY]['profit_floor_price']
        observe(pos, 96.0)
        assert pos[STATE_KEY]['profit_floor_price'] == pytest.approx(96.0 + 0.25 * 2.0)
        assert pos[STATE_KEY]['profit_floor_price'] < floor_after_first

    def test_S5_pullback_before_floor_hold(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        observe(pos, 96.0)
        floor = pos[STATE_KEY]['profit_floor_price']
        assert observe(pos, floor - 0.01) is None

    def test_S6_floor_hit_triggers_close(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        observe(pos, 96.0)
        floor = pos[STATE_KEY]['profit_floor_price']
        result = observe(pos, floor)
        assert result is not None
        assert result['trigger'] == 'EXIT_PROFIT_LOCK_FLOOR'

    def test_S7_floor_never_increases(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        observe(pos, 96.0)
        low_floor = pos[STATE_KEY]['profit_floor_price']
        observe(pos, 96.4)
        assert pos[STATE_KEY]['profit_floor_price'] == pytest.approx(low_floor)

    def test_S8_persistence_in_memory(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        observe(pos, 96.0)
        saved_state = copy.deepcopy(pos[STATE_KEY])
        pos2 = make_position('SHORT', 100, entry_atr=2.0)
        pos2[STATE_KEY] = saved_state
        assert pos2[STATE_KEY].get('profit_floor_armed') is True
        assert pos2[STATE_KEY]['profit_floor_price'] == pytest.approx(saved_state['profit_floor_price'])


# ==================================================
# FREEZE POLICY: global change must not affect armed position
# ==================================================

class TestFreezePolicyLong:

    def test_long_freeze_global_change_ignored(self, monkeypatch):
        """After arming with trail=0.25, changing global to trail=1.0 must not widen floor."""
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        observe(pos, 102.0)  # arm → frozen_trailing_distance_atr=0.25, floor=101.5
        assert pos[STATE_KEY]['frozen_trailing_distance_atr'] == pytest.approx(0.25)

        # Change global trail to 1.0 AFTER arming
        monkeypatch.setattr('core.services.exits.peak_trailing_exit.TRAILING_DISTANCE_ATR', 1.0)
        observe(pos, 104.0)  # new peak
        # With frozen 0.25: floor = 104 - 0.25*2 = 103.5
        # With leaked 1.0:  floor = 104 - 1.0*2  = 102.0 (would be lower → should be rejected)
        assert pos[STATE_KEY]['profit_floor_price'] == pytest.approx(103.5)

    def test_long_freeze_new_position_uses_new_config(self, monkeypatch):
        """A brand-new position arms with the current global trail value."""
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos_old = make_position('LONG', 100, entry_atr=2.0)
        observe(pos_old, 102.0)

        monkeypatch.setattr('core.services.exits.peak_trailing_exit.TRAILING_DISTANCE_ATR', 0.50)
        pos_new = make_position('LONG', 200, entry_atr=2.0)
        observe(pos_new, 202.0)  # new position arms with trail=0.50
        assert pos_new[STATE_KEY]['frozen_trailing_distance_atr'] == pytest.approx(0.50)


class TestFreezePolicyShort:

    def test_short_freeze_global_change_ignored(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        observe(pos, 98.0)   # arm → frozen_trailing_distance_atr=0.25, floor=98.5
        assert pos[STATE_KEY]['frozen_trailing_distance_atr'] == pytest.approx(0.25)

        monkeypatch.setattr('core.services.exits.peak_trailing_exit.TRAILING_DISTANCE_ATR', 1.0)
        observe(pos, 96.0)   # new favorable low
        # With frozen 0.25: floor = 96 + 0.25*2 = 96.5
        # With leaked 1.0:  floor = 96 + 1.0*2  = 98.0 (would be higher → should be rejected)
        assert pos[STATE_KEY]['profit_floor_price'] == pytest.approx(96.5)


# ==================================================
# JSON ROUND-TRIP RESTART PERSISTENCE
# ==================================================

class TestRestartPersistenceLong:

    def test_long_restart_armed_preserved(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        observe(pos, 104.0)
        pre_floor = pos[STATE_KEY]['profit_floor_price']
        pre_frozen_trail = pos[STATE_KEY]['frozen_trailing_distance_atr']

        pos2 = json_round_trip(pos)  # serialise → JSON → reload

        assert pos2[STATE_KEY].get('profit_floor_armed') is True, "profit_floor_armed not preserved"
        assert pos2[STATE_KEY]['profit_floor_price'] == pytest.approx(pre_floor), "profit_floor_price not preserved"
        assert pos2[STATE_KEY]['frozen_trailing_distance_atr'] == pytest.approx(pre_frozen_trail), "frozen trail not preserved"
        assert pos2[STATE_KEY]['frozen_lock_arm_atr'] == pytest.approx(1.0), "frozen arm not preserved"

    def test_long_restart_floor_hit_works(self, monkeypatch):
        """After restart, floor hit still triggers close."""
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        observe(pos, 104.0)
        floor = pos[STATE_KEY]['profit_floor_price']

        pos2 = json_round_trip(pos)
        result = evaluate_peak_trailing(pos2, floor, 61000, fee=0.0, slippage=0.0)
        assert result is not None
        assert result['trigger'] == 'EXIT_PROFIT_LOCK_FLOOR'

    def test_long_monotonic_after_restart(self, monkeypatch):
        """LONG floor never decreases even after restart with changed global trail."""
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        observe(pos, 104.0)
        pre_floor = pos[STATE_KEY]['profit_floor_price']  # 103.5

        pos2 = json_round_trip(pos)
        # Change global to looser trail — frozen value must override
        monkeypatch.setattr('core.services.exits.peak_trailing_exit.TRAILING_DISTANCE_ATR', 2.0)
        observe(pos2, 105.0)  # new peak; with frozen 0.25: floor = 104.5; with leaked 2.0: floor=101.0
        assert pos2[STATE_KEY]['profit_floor_price'] >= pre_floor, "Floor decreased after restart"
        assert pos2[STATE_KEY]['profit_floor_price'] == pytest.approx(104.5)

    def test_long_restart_peak_state_preserved(self, monkeypatch):
        """peak_price survives JSON round-trip."""
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('LONG', 100, entry_atr=2.0)
        observe(pos, 104.0)
        pre_peak = pos[STATE_KEY]['peak_price']

        pos2 = json_round_trip(pos)
        assert pos2[STATE_KEY].get('peak_price') == pytest.approx(pre_peak)


class TestRestartPersistenceShort:

    def test_short_restart_armed_preserved(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        observe(pos, 96.0)
        pre_floor = pos[STATE_KEY]['profit_floor_price']
        pre_frozen_trail = pos[STATE_KEY]['frozen_trailing_distance_atr']

        pos2 = json_round_trip(pos)

        assert pos2[STATE_KEY].get('profit_floor_armed') is True
        assert pos2[STATE_KEY]['profit_floor_price'] == pytest.approx(pre_floor)
        assert pos2[STATE_KEY]['frozen_trailing_distance_atr'] == pytest.approx(pre_frozen_trail)
        assert pos2[STATE_KEY]['frozen_lock_arm_atr'] == pytest.approx(1.0)

    def test_short_restart_floor_hit_works(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        observe(pos, 96.0)
        floor = pos[STATE_KEY]['profit_floor_price']

        pos2 = json_round_trip(pos)
        result = evaluate_peak_trailing(pos2, floor, 61000, fee=0.0, slippage=0.0)
        assert result is not None
        assert result['trigger'] == 'EXIT_PROFIT_LOCK_FLOOR'

    def test_short_monotonic_after_restart(self, monkeypatch):
        """SHORT floor never increases even after restart with changed global trail."""
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        observe(pos, 96.0)
        pre_floor = pos[STATE_KEY]['profit_floor_price']  # 96.5

        pos2 = json_round_trip(pos)
        monkeypatch.setattr('core.services.exits.peak_trailing_exit.TRAILING_DISTANCE_ATR', 2.0)
        observe(pos2, 95.0)  # new favorable low; frozen 0.25: floor=95.5; leaked 2.0: floor=99.0
        assert pos2[STATE_KEY]['profit_floor_price'] <= pre_floor, "Floor increased after restart"
        assert pos2[STATE_KEY]['profit_floor_price'] == pytest.approx(95.5)

    def test_short_restart_peak_state_preserved(self, monkeypatch):
        enable_model_t(monkeypatch, arm=1.0, trail=0.25)
        pos = make_position('SHORT', 100, entry_atr=2.0)
        observe(pos, 96.0)
        pre_peak = pos[STATE_KEY]['peak_price']

        pos2 = json_round_trip(pos)
        assert pos2[STATE_KEY].get('peak_price') == pytest.approx(pre_peak)


# ==================================================
# REGRESSION: pullback suppress, hard stop, waterfall
# ==================================================

def test_armed_suppresses_peak_pullback_only(monkeypatch):
    enable_model_t(monkeypatch, arm=1.0, trail=0.50)
    pos = make_position('LONG', 100, entry_atr=2.0)
    observe(pos, 102.0)
    assert pos[STATE_KEY].get('profit_floor_armed')
    # Floor = 102 - 0.50*2 = 101.0; price=101.5 → above floor, pullback pressure suppressed
    assert observe(pos, 101.5) is None


def test_armed_does_not_suppress_waterfall(monkeypatch):
    enable_model_t(monkeypatch, arm=1.0, trail=0.25)
    pos = make_position('LONG', 100, entry_atr=2.0)
    observe(pos, 102.0)
    snap = dict(quote_ms=61000, live_bar_ms=60000, closed_bar_ms=0,
                live_open=102.0, atr=2.0)
    result = evaluate_peak_trailing(pos, 98.6, snap, fee=0.0, slippage=0.0)
    assert result is not None
    assert result['trigger'] == 'WATERFALL_DROP'


def test_hard_stop_preserved(monkeypatch):
    enable_model_t(monkeypatch, arm=1.0, trail=0.25)
    pos = make_position('LONG', 100, entry_atr=2.0)
    pos['initial_sl'] = 94.0
    result = observe(pos, 93.5)
    assert result is not None
    assert result['trigger'] == 'INITIAL_ATR'


def test_entry_atr_is_frozen_not_live(monkeypatch):
    """ARM uses frozen state['atr'] (from entry_atr), not the live atr kwarg."""
    enable_model_t(monkeypatch, arm=1.0, trail=0.25)
    pos = make_position('LONG', 100, entry_atr=4.0)
    # With live atr=0.5: (102-100)/0.5=4.0 → would arm; with entry_atr=4.0: 0.5 → must NOT arm
    result = evaluate_peak_trailing(pos, 102.0, 61000, atr=0.5, fee=0.0, slippage=0.0)
    assert result is None
    assert not pos[STATE_KEY].get('profit_floor_armed', False)
