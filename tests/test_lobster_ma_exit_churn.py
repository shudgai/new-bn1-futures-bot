"""Retired MA turns must never close or retry a position."""
import pytest
from core.services.exits.peak_trailing_exit import (
    STATE_KEY, HARD_REASON, PEAK_REASON, evaluate_peak_trailing, migrate_peak_state,
)


def position(side):
    return dict(side=side, entry_price=100., qty=1., open_timestamp=60., entry_atr=1.)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('gain,fee', [(0.6, 0.), (3., 0.02)])
def test_single_ma_turn_has_no_close_authority(side, gain, fee):
    # Arrange: cover both positive net profit and the legacy nonpositive-net branch.
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    snap = dict(quote_ms=61000, ma3=100-sign, last_ma3=100.,
                ma5=100-sign, last_ma5=100.)
    # Act: price is at its observed peak despite an adverse MA turn.
    result = evaluate_peak_trailing(p, 100+sign*gain, snap, fee=fee, slippage=0.)
    # Assert
    assert result is None
    assert 'pending' not in p[STATE_KEY]


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('trigger', ['EXIT_PEAK_MA_TURN_PRESSURE', 'EXIT_PARABOLIC_MA3_TURN'])
def test_retired_pending_ticket_is_revoked_in_both_stores(side, trigger):
    p = position(side)
    state = migrate_peak_state(p)
    state.update(peak_price=100.5, pending=PEAK_REASON, trigger=trigger)
    meta = {STATE_KEY: dict(state)}
    p.pop(STATE_KEY)
    result = migrate_peak_state(p, meta)
    assert result['peak_price'] == 100.5
    assert 'pending' not in result
    assert 'trigger' not in result


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_channel_swing_pullback_is_disabled_but_hard_stop_remains(side):
    p = position(side)
    p["entry_mode"] = "CHANNEL_SWING"
    sign = 1 if side == 'LONG' else -1
    assert evaluate_peak_trailing(p, 100+sign*1.2, 61000, fee=0., slippage=0.) is None
    result = evaluate_peak_trailing(p, 100+sign*0.69, 62000, fee=0., slippage=0.)
    assert result is None
    result = evaluate_peak_trailing(position(side), 100-sign*1.6, 61000, fee=0., slippage=0.)
    assert result['reason'] == HARD_REASON
