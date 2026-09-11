"""Post-close sequencing and MA3 turns must hold through real execution paths."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from core.services.exits.profit_protection_service import protection
from core.engine import TradingEngine
from test_channel_outer_cycle import setup as market
from test_channel_live_ma3_exit import setup as turn_market
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def timed_market(side):
    frame, price = market(side)
    frame['timestamp'] = [60_000 * (i + 1) for i in range(len(frame))]
    frame['atr'] = 6.
    return frame, price


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('location', ['inside', 'outside'])
@pytest.mark.parametrize('close_ok', [False, True])
async def test_profit_close_cannot_reopen_until_later_pullback_and_reclaim(side, location, close_ok, monkeypatch):
    frame, price = timed_market(side)
    if location == 'inside':
        frame['kc_upper'], frame['kc_lower'] = 110., 90.
    e = _execution_engine(frame, side, close_ok)
    e.account.save_state = lambda: None
    pos = e.account.positions[SYMBOL]
    pos.update(entry_price=100., qty=2., open_timestamp=1.)
    protection(pos, 110. if side == 'LONG' else 90., .0005, .0001)
    e.tickers[SYMBOL] = price
    e._channel_swing_action = lambda *a, **kw: {'action': 'HOLD'}
    e._abnormal_market_entry_allowed = lambda *a, **kw: True
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert len(e.account.events) == 1
    assert 'PROFIT_PROTECTION' in e.account.events[0][3]
    if not close_ok:
        assert SYMBOL in e.account.positions
        assert SYMBOL not in e.account.channel_profit_reentries
        return
    ticket = e.account.channel_profit_reentries[SYMBOL]
    assert ticket['phase'] == 'closed' and ticket['requires_pullback']
    # Even a same-candle return to CK followed by a strong reclaim cannot reopen.
    assert not e._profit_reentry_ready(SYMBOL, ticket, frame, 100.)
    assert 'pullback_bar' not in ticket
    fresh, recovered = timed_market(side)
    fresh['timestamp'] += 60_000
    e.fetch_klines = AsyncMock(return_value=fresh)
    e.tickers[SYMBOL] = recovered
    await e._try_profit_reentry(SYMBOL, fresh, recovered, False)
    assert len(e.account.events) == 1  # No observed pullback yet.
    await e._try_profit_reentry(SYMBOL, fresh, 100., False)
    assert len(e.account.events) == 1
    e.account.channel_profit_reentries = json.loads(json.dumps(e.account.channel_profit_reentries))
    await asyncio.gather(*(e._try_profit_reentry(SYMBOL, fresh, recovered, False) for _ in range(3)))
    assert [event[0] for event in e.account.events] == ['close', 'open'], e.account.logs
    assert e.account.events[-1][2] == side
    assert SYMBOL not in e.account.channel_profit_reentries


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('cached', [False, True])
async def test_ordinary_signal_cannot_bypass_persistent_recovery_ticket(side, cached, monkeypatch):
    frame, price = timed_market(side)
    e = _execution_engine(frame, side, True)
    e.account.positions.clear()
    e.account.channel_profit_reentries = {SYMBOL: dict(side=side, phase='closed', token='wait', exit_bar_id=1_200_000)}
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    snapshot = {'frame': frame, 'price': price, 'kc_upper': 103., 'kc_lower': 97., 'signal_code': 'KC_OUTSIDE_' + side}
    signal = {'side': side, 'entry_mode': 'CHANNEL_SWING', 'action': 'ENTER_MARKET'}
    assert not await e._place_structured_entry(SYMBOL, signal, price, channel_snapshot=snapshot if cached else None)
    assert not e.account.events


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('missing_exit', [False, True])
def test_old_ticket_migration_discards_old_pullback(side, missing_exit):
    frame, price = timed_market(side)
    e = _execution_engine(frame, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    ticket = dict(side=side, token='old', phase='closed', pullback_bar=1_140_000)
    if not missing_exit: ticket['exit_bar_id'] = 1_140_000
    assert not e._profit_reentry_ready(SYMBOL, ticket, frame, price)
    assert 'pullback_bar' not in ticket
    assert ticket['requires_pullback']
    frame['timestamp'] += 60_000
    assert not e._profit_reentry_ready(SYMBOL, ticket, frame, price)
    assert not e._profit_reentry_ready(SYMBOL, ticket, frame, 100.)
    ticket = json.loads(json.dumps(ticket))
    assert e._profit_reentry_ready(SYMBOL, ticket, frame, price)


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_fresh_order_inside_channel_cancels_previously_valid_reclaim(side, monkeypatch):
    frame, price = timed_market(side)
    e = _execution_engine(frame, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    ticket = dict(side=side, phase='closed', token='fresh', mode='outer_cycle', requires_pullback=True,
                  exit_bar_id=1_080_000, pullback_bar=1_140_000)
    e.account.channel_profit_reentries = {SYMBOL: ticket}
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    e.tickers[SYMBOL] = 100.  # Fresh ticker has returned inside CK before ordering.
    await e._try_profit_reentry(SYMBOL, frame, price, False)
    assert not e.account.events
    assert ticket['pullback_bar'] == 1_200_000
    assert SYMBOL in e.account.channel_profit_reentries


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('armed', [False, True])
@pytest.mark.parametrize('close_ok', [False, True])
async def test_inside_ma3_turn_closes_at_live_price_and_retries(side, armed, close_ok):
    frame, position, price = turn_market(side)
    frame['kc_upper'], frame['kc_lower'] = 110., 90.
    e = _execution_engine(frame, side, close_ok)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(position)
    if armed:
        e.account.positions[SYMBOL]['channel_profit_protection'] = {
            'identity': [side, position['open_timestamp'], position['entry_price'], position['qty']],
            'armed': True, 'peak_gross': 10.,
        }
    e._channel_swing_action = lambda *a, **kw: {'action': 'HOLD'}
    e.tickers[SYMBOL] = price
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert len(e.account.events) == 1
    assert e.account.events[0][2] == price
    assert e.account.events[0][3].endswith('LIVE_MA3_TURN_EXIT')
    if not close_ok:
        e.account.positions[SYMBOL] = json.loads(json.dumps(e.account.positions[SYMBOL]))
        frame['timestamp'] += 60_000
        e.tickers[SYMBOL] = position['entry_price']
        await e._process_single_symbol(SYMBOL, 3., None, False)
        assert len(e.account.events) == 2
        assert all(event[0] == 'close' for event in e.account.events)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_legacy_outer_helper_cannot_exit_on_pre_entry_turn(side):
    frame, position, price = turn_market(side)
    position.update(entry_kc_upper=100.5, entry_kc_lower=99.5, open_timestamp=1201.)
    frame['kc_upper'], frame['kc_lower'] = 110., 90.
    assert not TradingEngine._channel_outer_ma3_turn_exit(position, frame, price)
    favorable = 103. if side == 'LONG' else 97.
    assert not TradingEngine._channel_outer_ma3_turn_exit(position, frame, favorable)
    assert TradingEngine._channel_outer_ma3_turn_exit(position, frame, price)


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_missing_ck_data_does_not_delay_valid_ma3_close(side):
    frame, position, price = turn_market(side)
    frame = frame.drop(columns=['kc_upper', 'kc_lower'])
    e = _execution_engine(frame, side, True)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(position)
    e._channel_swing_action = lambda *a, **kw: {'action': 'HOLD'}
    e.tickers[SYMBOL] = price
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert len(e.account.events) == 1, e.account.logs
    assert e.account.events[0][3].endswith('LIVE_MA3_TURN_EXIT')
    assert SYMBOL not in e.account.positions


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('success', [False, True])
async def test_profitable_inside_ma3_exit_persists_wait_before_closing(side, success):
    frame, position, price = turn_market(side)
    frame['kc_upper'], frame['kc_lower'] = 110., 90.
    position['entry_price'] = 95. if side == 'LONG' else 105.
    e = _execution_engine(frame, side, success)
    saved = []
    e.account.save_state = lambda: saved.append(json.loads(json.dumps(getattr(e.account, 'channel_profit_reentries', {}))))
    e.account.positions[SYMBOL].update(position)
    e._channel_swing_action = lambda *a, **kw: {'action': 'HOLD'}
    e.tickers[SYMBOL] = price
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert len(e.account.events) == 1
    closing = next(s[SYMBOL] for s in saved if s.get(SYMBOL, {}).get('phase') == 'closing')
    assert closing['requires_pullback']
    assert closing['close_reason'].endswith('LIVE_MA3_TURN_EXIT')
    assert closing['close_requested_at_ms'] > 0
    assert (SYMBOL in e.account.channel_profit_reentries) is success
    if success:
        assert e.account.channel_profit_reentries[SYMBOL]['phase'] == 'closed'
        await e._try_profit_reentry(SYMBOL, frame, price, False)
        assert len(e.account.events) == 1
