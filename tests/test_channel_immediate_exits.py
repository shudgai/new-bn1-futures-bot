"""Live adverse abnormalities and MA3 turns exit without candle confirmation."""
import json
from unittest.mock import AsyncMock

import pytest
from core.engine import TradingEngine
from test_channel_protected_only_exit import market
from test_channel_swing_execution import _execution_engine, SYMBOL
from test_channel_ma3_outer_exit import fixture


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('amount,expected', [(.1, None), (.49, None), (.5, 'ABNORMAL'), (.6, 'ABNORMAL'), (1., 'WATERFALL')])
def test_single_live_body_uses_quote_not_stale_candle(side, amount, expected):
    f, _ = market(side, 'normal')
    f['open'] = f['close'] = 100.
    p = dict(side=side, entry_price=100., open_timestamp=1.)
    sign = 1 if side == 'SHORT' else -1
    result = TradingEngine._channel_exception_exit(p, f, 100 + sign * amount)
    assert result is None if expected is None else result.endswith(expected)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_entry_candle_measures_only_move_since_entry(side):
    f, _ = market(side, 'normal')
    f['open'] = f['close'] = 100.
    sign = 1 if side == 'SHORT' else -1
    p = dict(side=side, entry_price=100 + sign * 2, open_timestamp=1201.)
    assert TradingEngine._channel_exception_exit(p, f, p['entry_price'] + sign * .1) is None
    assert TradingEngine._channel_exception_exit(p, f, p['entry_price'] + sign * .6).endswith('ABNORMAL')


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('kind', ['ordinary', 'abnormal', 'waterfall', 'ma3'])
@pytest.mark.parametrize('armed', [False, True])
async def test_quote_monitor_closes_on_same_unfinished_bar(side, kind, armed, monkeypatch):
    f, _ = market(side, 'normal')
    sign = 1 if side == 'LONG' else -1
    price = 100 - sign * {'ordinary': .1, 'abnormal': .6, 'waterfall': 1.1, 'ma3': .1}[kind]
    p = dict(side=side, entry_price=100., qty=1., open_timestamp=1.)
    if kind == 'ma3':
        f, p, price = fixture(side)
        f['atr'] = 100.
    if armed:
        # Keep profit far from its retracement line; only the technical exit acts.
        p['entry_price'] = price - sign * 5
        p['channel_profit_protection'] = dict(
            identity=[side, p['open_timestamp'], p['entry_price'], p['qty']],
            armed=True, peak_gross=5.)
    e = _execution_engine(f, side, True)
    e.is_running = True
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(p)
    e._channel_exit_frames = {SYMBOL: f.copy()}
    e.fetch_klines = AsyncMock(side_effect=AssertionError('same-bar quote must use cache'))
    monkeypatch.setattr('core.engine.time.time', lambda: 1201.)
    await e._channel_quote_exit(SYMBOL, price, 1201000)
    assert len(e.account.events) == int(kind != 'ordinary'), e.account.logs
    if kind != 'ordinary':
        assert e.account.events[0][2] == price
        expected = 'LIVE_MA3_TURN_EXIT' if kind == 'ma3' else 'LIVE_ADVERSE_' + kind.upper()
        assert e.account.events[0][3].endswith(expected)
        assert e.account.channel_profit_reentries[SYMBOL]['requires_pullback'] is (kind != 'ma3')
        assert not e.account.positions


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_live_abnormal_pending_survives_recovery_and_retry(side, monkeypatch):
    f, _ = market(side, 'normal')
    e = _execution_engine(f, side, False)
    e.is_running = True
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(entry_price=100., qty=1., open_timestamp=1.)
    e._channel_exit_frames = {SYMBOL: f}
    monkeypatch.setattr('core.engine.time.time', lambda: 1201.)
    await e._channel_quote_exit(SYMBOL, 99.4 if side == 'LONG' else 100.6, 1201000)
    assert len(e.account.events) == 1, e.account.logs
    e.account.positions[SYMBOL] = json.loads(json.dumps(e.account.positions[SYMBOL]))
    await e._channel_quote_exit(SYMBOL, 100., 1201000)
    assert len(e.account.events) == 2
    assert e.account.events[0][3] == e.account.events[1][3]


@pytest.mark.anyio
@pytest.mark.parametrize('case', ['stale_quote', 'old_bar', 'flat'])
async def test_quote_monitor_rejects_stale_data_and_never_opens(case, monkeypatch):
    f, _ = market('LONG', 'normal')
    e = _execution_engine(f, 'LONG', True)
    e.is_running = True
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(entry_price=100., qty=1., open_timestamp=1.)
    e._channel_exit_frames = {SYMBOL: f}
    e.fetch_klines = AsyncMock(return_value=f)
    if case == 'flat': e.account.positions.clear()
    now = 1261. if case == 'old_bar' else 1201.
    monkeypatch.setattr('core.engine.time.time', lambda: now)
    await e._channel_quote_exit(SYMBOL, 99.4, (now - 6 if case == 'stale_quote' else now)*1000)
    assert not e.account.events


@pytest.mark.anyio
async def test_rollover_fetches_current_candle_before_exit(monkeypatch):
    f, _ = market('LONG', 'normal')
    e = _execution_engine(f, 'LONG', True)
    e.is_running = True
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(entry_price=100., qty=1., open_timestamp=1.)
    e._channel_exit_frames = {SYMBOL: f}
    fresh = f.copy()
    fresh['timestamp'] += 60000
    e.fetch_klines = AsyncMock(return_value=fresh)
    monkeypatch.setattr('core.engine.time.time', lambda: 1261.)
    await e._channel_quote_exit(SYMBOL, 99.4, 1261000)
    assert len(e.account.events) == 1, e.account.logs
    e.fetch_klines.assert_awaited_once()
    assert e.account.channel_profit_reentries[SYMBOL]['exit_bar_id'] == 1260000


@pytest.mark.anyio
async def test_concurrent_scan_and_quotes_close_once(monkeypatch):
    import asyncio
    f, _ = market('LONG', 'normal')
    e = _execution_engine(f, 'LONG', True)
    e.is_running = True
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(entry_price=100., qty=1., open_timestamp=1.)
    e._channel_exit_frames = {SYMBOL: f}
    monkeypatch.setattr('core.engine.time.time', lambda: 1201.)
    await asyncio.gather(*(e._channel_quote_exit(SYMBOL, 99.4, 1201000) for _ in range(3)))
    assert len(e.account.events) == 1
    assert e.account.events[0][0] == 'close'


@pytest.mark.anyio
async def test_websocket_dispatches_held_quote_to_immediate_exit(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    f, _ = market('LONG', 'normal')
    e = _execution_engine(f, 'LONG', True)
    e.is_running = True
    e.ws_exchange = SimpleNamespace(watch_tickers=AsyncMock(side_effect=[
        {SYMBOL + ':USDT': dict(last=99.4, timestamp=1201000)}, asyncio.CancelledError()]))
    e._update_market_surveillance = lambda *a: None
    e._channel_quote_exit = AsyncMock()
    monkeypatch.setattr('core.engine.BTC_FLASH_CRASH_DROP_PCT', 0)
    monkeypatch.setattr('core.engine.BTC_FLASH_CRASH_PUMP_PCT', 0)
    await e._ticker_loop()
    e._channel_quote_exit.assert_awaited_once_with(SYMBOL, 99.4, 1201000)
