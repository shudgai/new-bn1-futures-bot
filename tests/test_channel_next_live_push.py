"""Closed outer break followed by an unfinished same-color push."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from core.engine import TradingEngine
from core.services.exits.profit_protection_service import trend_style
from test_channel_profit_protection import styled_frame
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def push_frame(side='LONG'):
    f = styled_frame('CHOPPY')
    f['open'] = f['close'] = 100.
    f['ma15'] = 100.
    f.loc[9, ['open', 'close']] = [99.8, 100.]  # First completed directional body.
    f.loc[10, ['open', 'close']] = [101.5, 103.]
    f.loc[11, ['open', 'close']] = [103., 103.]  # Ticker, not stale close, pushes.
    f['high'] = f[['open', 'close']].max(axis=1) + .1
    f['low'] = f[['open', 'close']].min(axis=1) - .1
    if side == 'LONG':
        f.loc[10, 'kc_upper'] = 102.1
    else:
        f.loc[10, 'kc_lower'] = 97.9
    if side == 'SHORT':
        for k in ('open', 'close', 'high', 'low', 'kc_upper', 'kc_lower'):
            f[k] = 200 - f[k]
        f['high'], f['low'] = f['low'].copy(), f['high'].copy()
        f['kc_upper'], f['kc_lower'] = f['kc_lower'].copy(), f['kc_upper'].copy()
    f.loc[9:11, 'ma3'] = (
        [102.8, 103.0, 103.2] if side == 'LONG' else [97.2, 97.0, 96.8]
    )
    return f


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_live_successor_cannot_replace_closed_confirmation(side):
    f = push_frame(side)
    price = 103.2 if side == 'LONG' else 96.8
    result = TradingEngine._channel_swing_action(f, price)
    assert result == dict(action='WAIT', side=None, reason='KC_SURGE_WAIT_TROUGH' if side == 'LONG' else 'KC_OUTSIDE_WAIT_NEXT_CANDLE')
    # No first-candle or same-close entry.
    assert TradingEngine._channel_swing_action(f, float(f.iloc[-2]['close']))['action'] == 'WAIT'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_breakout_body_alone_is_not_enough(side):
    f = push_frame(side)
    if side == 'LONG':
        f.loc[10, ['open', 'close']] = [100.9, 103.3]
        price = 103.5
    else:
        f.loc[10, ['open', 'close']] = [99.0, 97.0]
        price = 96.8
    f['high'] = f[['open', 'close']].max(axis=1) + .1
    f['low'] = f[['open', 'close']].min(axis=1) - .1
    result = TradingEngine._channel_swing_action(f, price)
    assert result['action'] == 'WAIT'
    assert result['side'] is None


@pytest.mark.parametrize('invalid', ['wick', 'gap', 'opposite', 'deep_overlap', 'first_live', 'bad_ma15', 'opposing_ma15', 'spike'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_live_push_does_not_bypass_shape_and_direction_guards(side, invalid):
    f = push_frame(side)
    sign = 1 if side == 'LONG' else -1
    price = 100 + sign * 3.2
    if invalid == 'wick': f.loc[10, 'close'] = 100 + sign
    if invalid == 'gap': f.loc[10, 'open'] = 100 + sign * 2.5
    if invalid == 'opposite': f.loc[11, 'open'] = 100 + sign * 3.3
    if invalid == 'deep_overlap': f.loc[11, 'open'] = 100 + sign * 2.
    if invalid == 'first_live': f.loc[10, ['open', 'close']] = 100.
    if invalid == 'bad_ma15': f.loc[8, 'ma15'] = float('nan')
    if invalid == 'opposing_ma15': f.loc[8:10, 'ma15'] = [100 + sign, 100, 100 - sign]
    if invalid == 'spike': f.loc[10, ['high', 'low']] = [110., 90.]
    assert TradingEngine._channel_swing_action(f, price)['action'] == 'WAIT'


def test_falling_waves_and_held_position_do_not_use_exception(monkeypatch):
    f = push_frame()
    monkeypatch.setattr(TradingEngine, '_channel_closed_waves_falling', lambda _: True)
    assert TradingEngine._channel_swing_action(f, 103.2)['action'] == 'WAIT'
    assert TradingEngine._channel_swing_action(f, 103.2, 'SHORT')['action'] == 'HOLD'


@pytest.mark.anyio
async def test_fresh_snapshot_rechecks_price_and_candidate():
    f = push_frame()
    e = _execution_engine(f, 'LONG', True)
    e.account.positions.clear()
    e.tickers[SYMBOL] = 103.2
    bar = e._channel_candidate_bar_id(f)
    assert await e._fresh_channel_entry_snapshot(SYMBOL, 'LONG', bar) is None
    e.tickers[SYMBOL] = 103.
    assert await e._fresh_channel_entry_snapshot(SYMBOL, 'LONG', bar) is None
    e.tickers[SYMBOL] = 103.2
    assert await e._fresh_channel_entry_snapshot(SYMBOL, 'LONG', 'old-bar') is None


@pytest.mark.anyio
async def test_scan_never_submits_removed_live_push():
    f = push_frame()
    e = _execution_engine(f, 'LONG', True)
    e.account.positions.clear()
    e.tickers[SYMBOL] = 103.2
    e._place_structured_entry = AsyncMock(return_value=True)
    await asyncio.gather(*(e._process_single_symbol(SYMBOL, 1., None, False) for _ in range(3)))
    e._place_structured_entry.assert_not_awaited()


@pytest.mark.anyio
async def test_daily_halt_does_not_submit_live_push():
    f = push_frame()
    e = _execution_engine(f, 'LONG', True)
    e.account.positions.clear()
    e._place_structured_entry = AsyncMock(return_value=True)
    assert not await e._execute_confirmed_channel_break(SYMBOL, f, 103.2, 'LONG', True)
    e._place_structured_entry.assert_not_awaited()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_two_closed_bodies_are_enough_for_stacking(side):
    f = styled_frame('STACKED', side)
    f.loc[8, 'close'] = f.loc[8, 'open']
    assert trend_style(f, side, 1.) == 'STACKED'
    f.loc[9, 'close'] = f.loc[9, 'open']
    assert trend_style(f, side, 1.) != 'STACKED'



def test_entry_during_second_body_keeps_breakout_context_for_tightening():
    from core.services.exits.profit_protection_service import protection
    f = styled_frame('STACKED')
    f['timestamp'] = [i * 60000 for i in range(len(f))]
    f.loc[8, 'close'] = f.loc[8, 'open']
    p = dict(side='LONG', entry_price=100., qty=2., open_timestamp=610.,
             reason='Channel Swing KC_NEXT_LIVE_PUSH_LONG next live candle push LONG')
    result = protection(p, 105., .0005, .0001, f)
    assert result['trend_style'] == 'STACKED'
    result = protection(p, 104.4, .0005, .0001, f)
    assert result['retracement_fraction'] == .10
    assert result['triggered']
