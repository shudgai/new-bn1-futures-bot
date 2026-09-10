"""Current rules: two closed bodies, confirmed reversals and single fills."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from core.engine import TradingEngine
from core.paper_account import PaperAccount
from test_direct_break_execution import setup_engine, confirm_break
from test_channel_swing_execution import SYMBOL
from channel_test_frames import closed_outer_entry_frame

def confirm_entry(frame, side):
    """Supply a valid two-body outer entry while preserving the caller's clock."""
    source = closed_outer_entry_frame(side, len(frame))
    for key in source.columns:
        frame[key] = source[key].to_numpy()
    # Dedup tests need a clear trend, without the removed live-push chop exemption.
    sign = 1 if side == "LONG" else -1
    for key in ("ma15", "kc_middle", "ema_20"):
        frame.loc[frame.index[-4:-1], key] = [100 - sign * .4, 100 - sign * .2, 100.]
    frame.loc[frame.index[-4:-1], "kc_upper"] = [102 - sign * .4, 102 - sign * .2, 102.]
    frame.loc[frame.index[-4:-1], "kc_lower"] = [98 - sign * .4, 98 - sign * .2, 98.]
    return float(frame.iloc[-1]["close"])

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('length', [5, 30, 70])
def test_available_ck_history_confirms_entry(setup_engine, side, length):
    _, f = setup_engine(side)
    price = confirm_entry(f, side)
    f = f.tail(length)
    assert TradingEngine._channel_swing_action(f, price)['side'] == side

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('held', [False, True])
@pytest.mark.parametrize('invalid', ['live_only', 'doji', 'opposite_colour', 'wick', 'gap'])
def test_invalid_bodies_cannot_enter_or_reverse(setup_engine, side, held, invalid):
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
    # Keep live body neutral so this shape test does not trigger the
    # separately authorized adverse long-candle exit.
    price = float(f.iloc[-1]['open'])
    f['high'] = f[['open', 'close', 'high']].max(axis=1)
    f['low'] = f[['open', 'close', 'low']].min(axis=1)
    if not held:
        # Isolate the invalid outer break from an independently valid pivot.
        f["ma3"] = 100.
    f['kc_middle'] = 100.
    sign = 1 if side == 'LONG' else -1
    f.loc[66:68, 'kc_middle'] = [100. - sign * .2, 100. - sign * .1, 100.]
    result = TradingEngine._channel_swing_action(f, price, old)
    assert result['action'] == ('HOLD' if held else 'WAIT')
    assert result['side'] is None

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('held', [False, True])
async def test_confirmed_signal_fills_once_on_same_scan(setup_engine, side, held, monkeypatch):
    old = ('SHORT' if side == 'LONG' else 'LONG') if held else None
    e, f = setup_engine(side, old)
    e._channel_chop_state = TradingEngine._channel_chop_state
    if held:
        confirm_break(f, side)
        sign = 1 if side == "LONG" else -1
        f.loc[66:68, "kc_middle"] = [100 - sign * .2, 100 - sign * .1, 100.]
        f.loc[67:68, "ma3"] = [100 + sign * 1., 100 + sign * 1.2]
        # setup_engine resets this candle's open/close to 100; its old trend
        # high/low must be reset too now every new leg validates market data.
        f.loc[66, ["high", "low"]] = [100.1, 99.9]
        # Successful reversal needs sustained directional energy, not fading volume.
        f.loc[66:68, "volume"] = [1., 10., 100.]
        f.loc[69, ["open", "close", "high", "low"]] = ([103.2, 103.25, 103.3, 103.1] if side == "LONG" else [96.8, 96.75, 96.9, 96.7])
        e.tickers[SYMBOL] = float(f.iloc[-1]["close"])
    else:
        e.tickers[SYMBOL] = confirm_entry(f, side)
    if held:
        # The new order must pass the existing MA15 direction revalidation.
        e.account.positions[SYMBOL]['channel_favorable_rail_reached'] = False
        pos = e.account.positions[SYMBOL]
        pos['channel_profit_protection'] = dict(armed=True, peak_gross=1.,
            identity=[pos['side'], pos['open_timestamp'], pos['entry_price'], pos['qty']])
    await asyncio.gather(*(e._process_single_symbol(SYMBOL, 1., None, False) for _ in range(3)))
    if held:
        assert SYMBOL not in e.account.positions
        assert [t['action'] for t in e.account.trades] == [f'CLOSE_{old}']
        # A confirmed reverse still closes immediately; its new leg waits a minute.
        import time
        next_minute = (int(time.time() // 60) + 1) * 60 + 1
        monkeypatch.setattr('core.engine.time.time', lambda: next_minute)
        await e._process_single_symbol(SYMBOL, 2., None, False)
    assert SYMBOL in e.account.positions, '\n'.join(row['text'] for row in e.account.logs)
    assert e.account.positions[SYMBOL]['side'] == side, e.account.logs
    actions = [t['action'] for t in reversed(e.account.trades)]
    assert actions == ([f'CLOSE_{old}'] if held else []) + [f'OPEN_{side}']
    assert e.account.positions[SYMBOL]['sl'] == 0.
    assert e.account.trades[0]['channel_confirmation_bar_id'] == f.iloc[-2].get('timestamp', f.index[-2])

@pytest.mark.anyio
@pytest.mark.parametrize('reload', [False, True])
async def test_closed_trade_cannot_reuse_confirmation_even_after_restart(setup_engine, reload, monkeypatch):
    e, f = setup_engine('LONG')
    e._channel_chop_state = TradingEngine._channel_chop_state
    e.tickers[SYMBOL] = confirm_entry(f, 'LONG')
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert SYMBOL in e.account.positions, '\n'.join(row['text'] for row in e.account.logs)
    assert await e.account.close_position(SYMBOL, 103., '手動平倉', is_manual=True)
    e.release_manual_close_state(SYMBOL)
    if reload:
        e.account = PaperAccount()
        del e._channel_used_confirmation
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert SYMBOL not in e.account.positions
    assert len(e.account.trades) == 2
    # Both a new confirmation and a new execution minute are required.
    import time
    next_minute = (int(time.time() // 60) + 1) * 60 + 1
    monkeypatch.setattr('core.engine.time.time', lambda: next_minute)
    f['timestamp'] = list(range(70))
    f.loc[68, 'timestamp'] = 999
    await e._process_single_symbol(SYMBOL, 3., None, False)
    assert e.account.positions[SYMBOL]['side'] == 'LONG', e.account.logs

@pytest.mark.anyio
async def test_expired_confirmation_cannot_open(setup_engine):
    e, f = setup_engine('LONG')
    e.tickers[SYMBOL] = confirm_entry(f, 'LONG')
    fresh = f.copy()
    fresh['timestamp'] = list(range(70))
    fresh.loc[68, 'timestamp'] = 999
    e.fetch_klines = AsyncMock(return_value=fresh)
    assert not await e._execute_confirmed_channel_break(SYMBOL, f, 103., 'LONG')
    assert not e.account.trades

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_rejected_reverse_retry_expires_with_bar(setup_engine, side):
    old = 'SHORT' if side == 'LONG' else 'LONG'
    e, f = setup_engine(side, old)
    pos = e.account.positions[SYMBOL]
    pos['channel_profit_protection'] = dict(armed=True, peak_gross=1.,
        identity=[pos['side'], pos['open_timestamp'], pos['entry_price'], pos['qty']])
    confirm_break(f, side)
    e._abnormal_market_entry_allowed = lambda *_: False
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert SYMBOL not in e.account.positions
    assert e._channel_outer_reentry_after_exit[SYMBOL] == side
    e._abnormal_market_entry_allowed = lambda *_: True
    f.loc[67, ['open','close']] = [103., 103.2] if side == 'LONG' else [97., 96.8]
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
