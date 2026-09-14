"""Pivot CK independence, closed-time chart replay and shared consolidation veto."""
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.strategies.outer_strategy import aligned_entry
from core.services.strategies.pivot_strategy import confirmed_ma3_pivot, pivot_entry
from core.services.swing_service import channel_chop_state
from core.services.entry_diagnostics_service import entry_diagnostics
from services.pivot_markers import build_pivot_markers
from test_channel_ma3_primary_entry import market
from test_channel_swing_execution import SYMBOL, _execution_engine


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def history(side: str, chop: str = '') -> pd.DataFrame:
    frame = market(side)
    padding = pd.concat([frame.iloc[[0]]] * 20, ignore_index=True)
    frame = pd.concat([padding, frame], ignore_index=True)
    frame['timestamp'] = [3_600_000 + i * 60_000 for i in range(len(frame))]
    sign = 1 if side == 'LONG' else -1
    frame.loc[frame.index[-2], 'kc_middle'] -= sign * .2
    if chop == 'compression':
        frame.loc[frame.index[-2], ['kc_upper', 'kc_lower']] = [101., 99.]
    elif chop == 'low_momentum':
        frame['kc_middle'] = 100.
    return frame


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('chop', ['', 'compression', 'low_momentum'])
def test_chart_and_entry_agree_on_real_consolidation(side: str, chop: str) -> None:
    frame = history(side, chop)
    price = float(frame.iloc[-1]['open'])
    assert confirmed_ma3_pivot(frame, price)['side'] == side
    assert channel_chop_state(frame)['detected'] is bool(chop)
    decision = aligned_entry(frame, price)
    markers = build_pivot_markers(frame)
    assert (decision['action'] == 'ENTER') is (not chop)
    assert (frame.index[-1] in markers) is (not chop)
    if chop:
        assert pivot_entry(frame, price)['reason'] == 'KC_CHOP_WAIT'
    else:
        marker = markers[frame.index[-1]][0]
        assert marker['side'] == decision['side']
        assert marker['reason'] == decision['reason']
        assert marker['timestamp'] == frame.iloc[-2]['timestamp'] + 60_000
        assert marker['timestamp'] == frame.iloc[-1]['timestamp']
        assert frame.index[-2] not in markers


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_marker_uses_open_quote_without_hourly_alignment(side: str) -> None:
    frame = history(side)
    expected = build_pivot_markers(frame)
    assert expected[frame.index[-1]][0]['alignment'] == ''
    # Later movement and indicators were unavailable when this opening signal formed.
    frame.loc[frame.index[-1], ['close', 'high', 'low', 'ma3', 'kc_upper', 'kc_lower']] = [1., 1000., .1, 1., 2., 3.]
    assert build_pivot_markers(frame) == expected
    assert frame.index[-1] not in build_pivot_markers(frame.iloc[:-1])


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_invalidated_pivot_opening_quote_has_no_marker(side: str) -> None:
    frame = history(side)
    frame.loc[frame.index[-1], 'open'] = frame.iloc[-3]['low' if side == 'LONG' else 'high']
    assert frame.index[-1] not in build_pivot_markers(frame)


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('chop', ['', 'compression', 'low_momentum'])
async def test_scanner_and_diagnostics_share_pivot_consolidation_gate(side: str, chop: str, monkeypatch) -> None:
    frame = history(side, chop)
    price = float(frame.iloc[-1]['close'])
    now = frame.iloc[-1]['timestamp'] / 1000 + 1
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine.is_running = True
    engine.tickers[SYMBOL] = price
    engine._channel_entry_quote_times = {SYMBOL: now}
    engine._channel_chop_state = channel_chop_state
    engine._abnormal_market_entry_allowed = lambda *a, **k: True
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    diagnostic = entry_diagnostics(engine, SYMBOL, frame, price, now)
    assert diagnostic['reason'] == ('KC_CHOP_WAIT' if chop else 'KC_ENTRY_READY')
    await engine._process_single_symbol(SYMBOL, now, None, False)
    assert len(engine.account.events) == (0 if chop else 1), engine.account.logs
    if not chop:
        assert engine.account.events[0][0:3:2] == ('open', side)


def test_twenty_rows_never_uses_forming_candle_for_chop() -> None:
    frame = history('LONG', 'low_momentum').tail(20)
    assert not channel_chop_state(frame)['detected']


@pytest.mark.anyio
@pytest.mark.parametrize('chop', ['', 'compression'])
async def test_chart_api_returns_shared_markers_without_orders(chop: str, monkeypatch) -> None:
    import services.api as api

    frame = history('LONG', chop)
    frame['ema_20'] = frame['kc_middle']
    engine = _execution_engine(frame, 'LONG', True)
    engine.account.trades = []
    engine.pivot_prealerts = {}
    engine.fetch_klines = AsyncMock(return_value=frame.copy())
    monkeypatch.setattr(api, 'engine', engine)
    response = await api._load_klines(SYMBOL, '1m', len(frame), False)
    engine.fetch_klines.assert_awaited_once_with(SYMBOL, timeframe='1m', limit=len(frame), keep_live=False)
    last = response['data'][-1]
    assert bool(last['pivot_markers']) is (not chop)
    if not chop:
        assert last['time'] * 1000 == last['pivot_markers'][0]['confirmed_at']
    assert not response['data'][-2]['pivot_markers']
    assert not engine.account.events


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_fresh_snapshot_cancels_pivot_when_market_compresses(side: str) -> None:
    frame = history(side)
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.tickers[SYMBOL] = float(frame.iloc[-1]['close'])
    assert await engine._fresh_channel_entry_snapshot(SYMBOL, side, 5, pivot_entry_signal=True)
    frame.loc[frame.index[-2], ['kc_upper', 'kc_lower']] = [101., 99.]
    assert await engine._fresh_channel_entry_snapshot(SYMBOL, side, 5, pivot_entry_signal=True) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_pivot_exit_remains_available_during_entry_consolidation(side: str) -> None:
    from core.services.strategies.outer_strategy import three_point_pivot_exit_ready

    frame = history(side, 'low_momentum')
    pivot_index = frame.index[-3]
    if side == 'LONG':
        frame.loc[pivot_index, 'low'] = frame['low'].min() - 1.
    else:
        frame.loc[pivot_index, 'high'] = frame['high'].max() + 1.
    price = float(frame.iloc[-1]['close'])
    assert pivot_entry(frame, price)['reason'] == 'KC_CHOP_WAIT'
    assert three_point_pivot_exit_ready(frame, 'SHORT' if side == 'LONG' else 'LONG', price)


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('hourly_direction', [1, -1, None])
async def test_first_closed_pivot_quote_enters_without_waiting_for_hourly_direction(side: str, hourly_direction, monkeypatch) -> None:
    frame = history(side)
    forming = frame.iloc[:-1].copy()
    engine = _execution_engine(forming, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine._channel_chop_state = channel_chop_state
    engine._abnormal_market_entry_allowed = lambda *a, **k: True
    engine.st_direction_1h_cache = {} if hourly_direction is None else {SYMBOL: hourly_direction}
    engine.btc_1h_st_direction = hourly_direction or 0
    engine.fetch_klines = AsyncMock(return_value=forming)
    engine.tickers[SYMBOL] = float(forming.iloc[-1]['close'])
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    # The right-hand candle is still forming; there is no confirmed pivot yet.
    await engine._process_single_symbol(SYMBOL, forming.iloc[-1]['timestamp'] / 1000 + 1, None, False)
    assert not engine.account.events
    # On the first quote after its close, no additional candle or hourly turn is required.
    engine.fetch_klines = AsyncMock(return_value=frame)
    engine.tickers[SYMBOL] = float(frame.iloc[-1]['open'])
    await engine._process_single_symbol(SYMBOL, frame.iloc[-1]['timestamp'] / 1000, None, False)
    assert len(engine.account.events) == 1, engine.account.logs
    assert engine.account.events[0][0:3:2] == ('open', side)
