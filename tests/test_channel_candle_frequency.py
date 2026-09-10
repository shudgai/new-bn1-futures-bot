"""One automatic entry per minute without delaying live recovery or exits."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from core.engine import TradingEngine
from test_channel_outer_cycle import setup as market
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def clock(monkeypatch):
    clock = [1_788_998_401.]
    monkeypatch.setattr('core.engine.time.time', lambda: clock[0])
    return clock


def ready_engine(side, monkeypatch):
    frame, price = market(side)
    frame['atr'] = 6.
    e = _execution_engine(frame, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = price
    e._abnormal_market_entry_allowed = lambda *a, **kw: True
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    signal = {'side': side, 'score': 100, 'entry_mode': 'CHANNEL_SWING', 'action': 'ENTER_MARKET', 'reason': 'test'}
    return e, frame, price, signal


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_same_minute_repeated_orders_and_new_tokens_cannot_reopen(side, monkeypatch, clock):
    e, frame, price, signal = ready_engine(side, monkeypatch)
    assert await e._place_structured_entry(SYMBOL, signal, price)
    e.account.positions.clear()
    e.account.channel_profit_reentries = {SYMBOL: dict(side=side, token='new', phase='closed',
        mode='outer_cycle', requires_pullback=True, exit_bar_id=17, pullback_bar=18)}
    await asyncio.gather(*(e._try_profit_reentry(SYMBOL, frame, price, False) for _ in range(4)))
    assert len(e.account.events) == 1
    clock[0] += 60
    await e._try_profit_reentry(SYMBOL, frame, price, False)
    assert len(e.account.events) == 2, e.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_failed_order_does_not_consume_minute(side, monkeypatch, clock):
    e, frame, price, signal = ready_engine(side, monkeypatch)
    e.account.open_position = AsyncMock(side_effect=[False, True])
    assert not await e._place_structured_entry(SYMBOL, signal, price)
    assert await e._place_structured_entry(SYMBOL, signal, price)
    assert not await e._place_structured_entry(SYMBOL, signal, price)
    assert e.account.open_position.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_concurrent_success_is_single_flight(side, monkeypatch, clock):
    e, frame, price, signal = ready_engine(side, monkeypatch)
    # No fake position is added: the minute limit itself prevents subsequent fills.
    e.account.open_position = AsyncMock(return_value=True)
    results = await asyncio.gather(*(e._place_structured_entry(SYMBOL, signal.copy(), price) for _ in range(5)))
    assert sum(results) == 1
    assert e.account.open_position.await_count == 1


@pytest.mark.parametrize('source', ['OPEN_LONG', 'OPEN_SHORT', 'CLOSE_LONG', 'CLOSE_SHORT', 'last_closed_at'])
def test_persisted_fills_block_both_directions_after_restart(source, clock):
    frame, _ = market('LONG')
    e = _execution_engine(frame, 'LONG', True)
    e.account.positions.clear()
    saved = {'trades': [], 'last_closed_at': {}}
    if source == 'last_closed_at': saved['last_closed_at'][SYMBOL] = clock[0]
    else: saved['trades'] = [dict(symbol=SYMBOL, action=source, id=int(clock[0] * 1000))]
    saved = json.loads(json.dumps(saved))
    e.account.trades = saved['trades']
    e.account.last_closed_at = saved['last_closed_at']
    assert e._channel_candle_entry_blocked(SYMBOL)
    assert not e._channel_candle_entry_blocked('OTHER/USDT')
    clock[0] += 60
    assert not e._channel_candle_entry_blocked(SYMBOL)


@pytest.mark.anyio
async def test_close_observed_during_await_blocks_final_order(monkeypatch, clock):
    e, frame, price, signal = ready_engine('LONG', monkeypatch)
    async def safe(*args):
        e.account.last_closed_at = {SYMBOL: clock[0]}
        return True
    e._execution_price_is_safe = safe
    assert not await e._place_structured_entry(SYMBOL, signal, price)
    assert not e.account.events


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('success', [False, True])
async def test_minute_limit_never_blocks_adverse_exit_or_failed_close_retry(side, success, clock):
    frame, _ = market(side)
    frame['timestamp'] = [(i+1)*60000 for i in range(len(frame))]
    frame['atr'] = 1.
    price = 99. if side == 'LONG' else 101.
    frame.loc[19, 'open'] = 105. if side == 'LONG' else 95.
    position = dict(side=side, entry_price=100., qty=1., open_timestamp=1.)
    e = _execution_engine(frame, side, success)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(position)
    e._channel_entry_minute = {SYMBOL: int(clock[0] // 60)}
    e.tickers[SYMBOL] = price
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert len(e.account.events) == 1
    assert e.account.events[0][0] == 'close'
    assert e.account.events[0][3].endswith('EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL')
    if not success:
        await e._process_single_symbol(SYMBOL, 2., None, False)
        assert len(e.account.events) == 2


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_reclaim_uses_two_existing_closed_bodies_after_later_pullback(side, monkeypatch, clock):
    e, frame, price, signal = ready_engine(side, monkeypatch)
    e.account.channel_profit_reentries = {SYMBOL: dict(side=side, token='now', phase='closed',
        mode='outer_cycle', requires_pullback=True, exit_bar_id=18)}
    await e._try_profit_reentry(SYMBOL, frame, 100., False)
    assert not e.account.events
    # No candle is added or closed between pullback and the new live reclaim.
    await e._try_profit_reentry(SYMBOL, frame, price, False)
    assert len(e.account.events) == 1, e.account.logs
    assert e.account.events[0][2] == side
