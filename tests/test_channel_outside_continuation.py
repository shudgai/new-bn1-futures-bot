"""A missed breakout can enter on later closed outside bodies, with existing gates."""
import pytest
from core.channel_outer_entry import (
    aligned_entry, aligned_entry_ready, outside_reentry,
    confirmed_outer_breakout_ready, confirmed_outer_continuation_ready,
)
from core.engine import TradingEngine
from test_channel_breakout_only import invalid_frame
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_two_outside_bodies_allow_continuation_without_new_cross(side):
    f, price = invalid_frame(side, 'no_cross')
    assert not confirmed_outer_breakout_ready(f, price, side)
    assert confirmed_outer_continuation_ready(f, price, side)
    assert aligned_entry(f, price)['side'] == side
    assert aligned_entry_ready(f, price, side)
    assert outside_reentry(f, price, side)['side'] == side
    assert TradingEngine._channel_swing_action(f, price)['side'] == side


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('offset', [-3, -2])
@pytest.mark.parametrize('touch', [False, True])
def test_both_closed_bodies_must_finish_strictly_outside(side, offset, touch):
    f, price = invalid_frame(side, 'no_cross')
    sign = 1 if side == 'LONG' else -1
    rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
    idx = f.index[offset]
    closed = float(f.loc[idx, rail]) - (0 if touch else sign*.1)
    f.loc[idx, ['open', 'close']] = [closed-sign*.4, closed]
    f['high'] = f[['open', 'close']].max(axis=1)+.1
    f['low'] = f[['open', 'close']].min(axis=1)-.1
    assert not confirmed_outer_continuation_ready(f, price, side)
    assert not aligned_entry_ready(f, price, side)
    assert outside_reentry(f, price, side)['action'] == 'WAIT'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('requires_pullback', [False, True])
def test_reentry_continuation_retains_abnormal_pullback_and_new_signal(side, requires_pullback):
    f, price = invalid_frame(side, 'no_cross')
    f['timestamp'] = [(i+1)*60000 for i in range(len(f))]
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    ticket = dict(side=side, phase='closed', mode='outer_cycle',
                  requires_pullback=requires_pullback, exit_bar_id=900000.)
    assert e._profit_reentry_ready(SYMBOL, ticket, f, price) == (not requires_pullback)
    if requires_pullback:
        assert not e._profit_reentry_ready(SYMBOL, ticket, f, 100.)
        assert e._profit_reentry_ready(SYMBOL, ticket, f, price)
    ticket['exit_bar_id'] = float(f.iloc[-1].timestamp)
    assert not e._profit_reentry_ready(SYMBOL, ticket, f, price)
