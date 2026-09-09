"""2026-09-08 rules: closed outer break, hold, confirmed reversal, single fill."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from core.engine import TradingEngine
from core.paper_account import PaperAccount
from test_direct_break_execution import setup_engine, confirm_break
from test_channel_swing_execution import SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('length', [4, 30, 70])
def test_available_ma15_history_confirms_entry(setup_engine, side, length):
    _, f = setup_engine(side)
    confirm_break(f, side)
    f = f.tail(length)
    assert TradingEngine._channel_swing_action(f, 100.)['side'] == side

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('held', [False, True])
@pytest.mark.parametrize('invalid', ['live_only', 'doji', 'opposite_colour', 'wick', 'gap'])
def test_unconfirmed_break_cannot_open_or_reverse(setup_engine, side, held, invalid):
    old = ('SHORT' if side == 'LONG' else 'LONG') if held else None
    _, f = setup_engine(side, old)
    # Isolate invalid breakout shapes from the separately tested emergency exits.
    f["atr"] = 10.
    confirm_break(f, side)
    if invalid == 'live_only':
        f.loc[69] = f.loc[68].copy()
        f.loc[68] = f.loc[67].copy()
        f.loc[67, ['open','close']] = [100.,100.]
    elif invalid == 'doji':
        f.loc[68, 'open'] = f.loc[68, 'close']
    elif invalid == 'opposite_colour':
        f.loc[68, 'open'] = 104. if side == 'LONG' else 96.
    elif invalid == 'wick':
        f.loc[67, 'close'] = 100.
    else:
        f.loc[67, 'open'] = 102.5 if side == 'LONG' else 97.5
    result = TradingEngine._channel_swing_action(f, 105., old)
    if invalid == 'live_only' and not held and side == 'LONG':
        # Newly authorized: the previous closed body broke the rail and the
        # immediate live successor pushes further. Held reversal still waits.
        assert result == {'action': 'ENTER', 'side': 'LONG', 'reason': 'KC_NEXT_LIVE_PUSH_LONG'}
    else:
        assert result['action'] == ('HOLD' if held else 'WAIT')

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('held', [False, True])
async def test_confirmed_signal_fills_once_on_same_scan(setup_engine, side, held):
    old = ('SHORT' if side == 'LONG' else 'LONG') if held else None
    e, f = setup_engine(side, old)
    confirm_break(f, side)
    if held:
        # Reversal does not require profit, MA alignment or first visiting own rail.
        f['ma15'] = list(reversed(f['ma15'].tolist()))
        e.account.positions[SYMBOL]['channel_favorable_rail_reached'] = False
    await asyncio.gather(*(e._process_single_symbol(SYMBOL, 1., None, False) for _ in range(3)))
    assert e.account.positions[SYMBOL]['side'] == side, e.account.logs
    actions = [t['action'] for t in reversed(e.account.trades)]
    assert actions == ([f'CLOSE_{old}'] if held else []) + [f'OPEN_{side}']
    assert e.account.positions[SYMBOL]['sl'] == 0.
    assert e.account.trades[0]['channel_confirmation_bar_id'] == 68

@pytest.mark.anyio
@pytest.mark.parametrize('reload', [False, True])
async def test_closed_trade_cannot_reuse_confirmation_even_after_restart(setup_engine, reload):
    e, f = setup_engine('LONG')
    confirm_break(f, 'LONG')
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert SYMBOL in e.account.positions
    assert await e.account.close_position(SYMBOL, 103., '手動平倉', is_manual=True)
    e.release_manual_close_state(SYMBOL)
    if reload:
        e.account = PaperAccount()
        del e._channel_used_confirmation
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert SYMBOL not in e.account.positions
    assert len(e.account.trades) == 2
    # An actual later confirmation is eligible.
    f['timestamp'] = list(range(70))
    f.loc[68, 'timestamp'] = 999
    await e._process_single_symbol(SYMBOL, 3., None, False)
    assert e.account.positions[SYMBOL]['side'] == 'LONG', e.account.logs

@pytest.mark.anyio
async def test_expired_confirmation_cannot_open(setup_engine):
    e, f = setup_engine('LONG')
    confirm_break(f, 'LONG')
    fresh = f.copy()
    fresh['timestamp'] = list(range(70))
    fresh.loc[68, 'timestamp'] = 999
    e.fetch_klines = AsyncMock(return_value=fresh)
    assert not await e._execute_confirmed_channel_break(SYMBOL, f, 103., 'LONG')
    assert not e.account.trades

@pytest.mark.anyio
async def test_rejected_reverse_retry_expires_with_bar(setup_engine):
    e, f = setup_engine('LONG', 'SHORT')
    confirm_break(f, 'LONG')
    e._abnormal_market_entry_allowed = lambda *_: False
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert SYMBOL not in e.account.positions
    assert e._channel_outer_reentry_after_exit[SYMBOL] == 'LONG'
    e._abnormal_market_entry_allowed = lambda *_: True
    f.loc[67, ['open','close']] = [103., 103.2]
    f['timestamp'] = list(range(70))
    f.loc[68, 'timestamp'] = 999
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert SYMBOL not in e.account.positions
    assert SYMBOL not in e._channel_outer_reentry_after_exit
    assert len(e.account.trades) == 1

@pytest.mark.anyio
async def test_background_trigger_only_updates_diagnostics(setup_engine, monkeypatch):
    import core.engine as em
    e, f = setup_engine('LONG', 'LONG')
    e.is_running = True
    e.position_triggers = {}
    e._soft_warning_since = {}
    e.account.positions[SYMBOL]['unrealized_pnl'] = 0.
    e.account.close_position = AsyncMock()
    monkeypatch.setattr(em, 'compute_position_trigger', lambda *_: {'atr': 1.})
    monkeypatch.setattr(em, 'drop_unclosed_candle', lambda frame, *_: frame)
    e._channel_adverse_exit_reason = lambda *_: 'EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL'
    async def stop(_):
        e.is_running = False
    monkeypatch.setattr(em.asyncio, 'sleep', stop)
    await e._position_trigger_loop()
    assert SYMBOL in e.position_triggers, e.account.logs
    e.account.close_position.assert_not_awaited()
    assert not any('暫時失敗' in row['text'] for row in e.account.logs)


@pytest.mark.anyio
async def test_expired_reverse_does_not_close_existing_position(setup_engine):
    e, f = setup_engine('LONG', 'SHORT')
    confirm_break(f, 'LONG')
    fresh = f.copy()
    fresh['timestamp'] = list(range(70))
    fresh.loc[68, 'timestamp'] = 999
    e.fetch_klines = AsyncMock(return_value=fresh)
    assert not await e._execute_confirmed_channel_break(SYMBOL, f, 103., 'LONG')
    assert e.account.positions[SYMBOL]['side'] == 'SHORT'
    assert not e.account.trades
