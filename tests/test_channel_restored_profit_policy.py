"""Net-profit trailing protection and no reversal under breakout-only entries."""
import copy
import json
from unittest.mock import AsyncMock

import pytest

from core.channel_profit_protection import protection
from core.channel_direct_reverse import authorized
from test_channel_fixed_steps import quote
from test_channel_protected_only_exit import market
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_net_peak_arms_at_one_and_exits_on_twenty_percent(side):
    p = dict(side=side, entry_price=100., qty=1., open_timestamp=1.)
    assert protection(p, quote(.99, side), .0005, .0001) is None
    result = protection(p, quote(1., side), .0005, .0001)
    assert result['locked_net'] == pytest.approx(.8)
    assert not result['triggered']
    result = protection(p, quote(5., side), .0005, .0001)
    assert result['locked_net'] == pytest.approx(4.)
    p = json.loads(json.dumps(p))
    assert not protection(p, quote(4.01, side), .0005, .0001)['triggered']
    assert protection(p, quote(3.99, side), .0005, .0001)['triggered']
    # A recovered price does not cancel an already triggered close retry.
    assert protection(p, quote(6., side), .0005, .0001)['triggered']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('pending', [False, True])
def test_fixed_policy_migration_keeps_known_net_peak_and_better_floor(side, pending):
    p = dict(side=side, entry_price=100., qty=1., open_timestamp=1.)
    p['channel_profit_protection'] = dict(identity=[side,1.,100.,1.],
        policy='fixed_net_steps_v1', peak_net=20., locked_net=18., armed=True, pending=pending)
    result = protection(p, quote(19., side), .0005, .0001)
    assert result['peak_net'] == 20.
    assert result['locked_net'] == 18.
    assert result['triggered'] == pending
    assert p['channel_profit_protection']['policy'] == 'net_peak_giveback_v1'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_new_position_does_not_inherit_old_protection(side):
    p = dict(side=side, entry_price=100., qty=1., open_timestamp=1.)
    protection(p, quote(5., side), .0005, .0001)
    p['open_timestamp'] = 2.
    assert protection(p, quote(.5, side), .0005, .0001) is None
    assert not p['channel_profit_protection']['armed']


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('success', [True, False])
async def test_net_profit_close_never_reverses_and_retries(side, success):
    f, _ = market(side, 'normal')
    e = _execution_engine(f, side, success)
    e.account.save_state = lambda: None
    p = e.account.positions[SYMBOL]
    p.update(entry_price=100., qty=1., open_timestamp=1.)
    protection(p, quote(2., side), .0005, .0001)
    e.tickers[SYMBOL] = quote(1.59, side)
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert [v[0] for v in e.account.events] == ['close'], e.account.logs
    assert 'PROFIT_PROTECTION' in e.account.events[0][3]
    if success:
        t = e.account.channel_profit_reentries[SYMBOL]
        assert t['side'] == side and t['mode'] == 'outer_cycle'
    else:
        assert p['channel_profit_protection']['pending']
        e.tickers[SYMBOL] = quote(3., side)
        await e._process_single_symbol(SYMBOL, 3., None, False)
        assert [v[0] for v in e.account.events] == ['close','close']


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('mode', ['direct_reverse', 'ck_reverse'])
async def test_old_reverse_ticket_is_inert_and_removed(side, mode):
    f, price = market(side, 'normal')
    e = _execution_engine(f, side, True)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL]['channel_reverse_wait_ck'] = True
    e.account.channel_profit_reentries = {SYMBOL: dict(mode=mode, phase='closed', side=side)}
    assert not authorized(e.account, SYMBOL, {}, 1.)
    assert not await e._try_ck_reverse(SYMBOL, f, price, False)
    assert not e.account.events
    assert SYMBOL not in e.account.channel_profit_reentries
    assert 'channel_reverse_wait_ck' not in e.account.positions[SYMBOL]
    e.account.positions.clear()
    e.account.channel_profit_reentries = {SYMBOL: dict(mode=mode, phase='closed', side=side)}
    await e._try_profit_reentry(SYMBOL, f, price, False)
    assert SYMBOL not in e.account.channel_profit_reentries
    assert not e.account.events


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_ck_and_ma_turn_do_not_exit(side):
    f, price = market(side, 'normal')
    e = _execution_engine(f, side, True)
    e.account.save_state = lambda: None
    p=e.account.positions[SYMBOL]
    p.update(entry_price=100., qty=1., open_timestamp=1.)
    p['channel_significant_ma3_turn'] = dict(pending=True)
    e.tickers[SYMBOL]=100.
    f['kc_middle'] = [100.+(i*.01 if side=='SHORT' else -i*.01) for i in range(len(f))]
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert not e.account.events
    assert 'channel_significant_ma3_turn' not in p
