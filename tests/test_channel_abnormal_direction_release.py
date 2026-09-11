"""Abnormal closes must not own the entry direction after CK turns."""
import copy
import json
from unittest.mock import AsyncMock

import pytest

from core.guards.abnormal_guard import opposite_entry_releases
from core.channel_entry_diagnostics import entry_diagnostics
from test_channel_aligned_entry import aligned_frame
from test_channel_swing_execution import _execution_engine, SYMBOL


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def setup(side):
    frame = aligned_frame(side, 'breakout')
    frame['timestamp'] = [(i + 1) * 60_000 for i in range(len(frame))]
    price = float(frame.iloc[-1]['close'])
    e = _execution_engine(frame, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = price
    old = 'SHORT' if side == 'LONG' else 'LONG'
    reason = 'Channel Swing EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL'
    e.account.channel_profit_reentries = {SYMBOL: dict(
        token='abnormal', phase='closed', side=old, mode='outer_cycle',
        requires_pullback=True, close_reason=reason,
        exit_bar_id=960000, close_requested_at_ms=960001)}
    e.account.trades = [dict(symbol=SYMBOL, action='CLOSE_'+old, reason=reason, id=960002)]
    e._channel_swing_peak_exit_info = {SYMBOL: dict(side=old, exit_bar_id=960000, require_new_closed_break=True, allow_new_outer_signal=True)}
    return e, frame, price


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['valid', 'restart', 'unmatched', 'wrong_side', 'wrong_reason',
    'old_fill', 'same_bar', 'delayed_fill_same_bar', 'closing', 'held', 'normal', 'manual',
    'direct', 'ck_flat', 'ma_reverse', 'fading', 'nan', 'adverse'])
def test_confirmed_opposite_direction_release(side, case):
    e, f, price = setup(side)
    ticket = e.account.channel_profit_reentries[SYMBOL]
    if case == 'restart':
        e.account.channel_profit_reentries = json.loads(json.dumps(e.account.channel_profit_reentries))
    elif case == 'unmatched': e.account.trades.clear()
    elif case == 'wrong_side': e.account.trades[0]['action'] = 'CLOSE_'+side
    elif case == 'wrong_reason': e.account.trades[0]['reason'] = 'manual'
    elif case == 'old_fill': e.account.trades[0]['id'] = 959999
    elif case == 'same_bar':
        ticket.update(exit_bar_id=1200000, close_requested_at_ms=1200001)
        e.account.trades[0]['id'] = 1200002
    elif case == 'delayed_fill_same_bar': e.account.trades[0]['id'] = 1200002
    elif case == 'closing': ticket['phase'] = 'closing'
    elif case == 'held': e.account.positions[SYMBOL] = {'side': side}
    elif case == 'normal': ticket['requires_pullback'] = False
    elif case == 'manual': ticket['close_reason'] = e.account.trades[0]['reason'] = 'manual'
    elif case == 'direct': ticket['mode'] = 'direct_reverse'
    elif case == 'ck_flat': f['kc_middle'] = 100.
    elif case == 'ma_reverse': price = float(f.iloc[-4]['close'])
    elif case == 'fading':
        sign = 1 if side == 'LONG' else -1
        f.loc[f.index[-5:-1], 'kc_middle'] = [100., 100.+sign*.3, 100.+sign*.5, 100.+sign*.6]
        f['kc_upper'] = f['kc_middle'] + 2
        f['kc_lower'] = f['kc_middle'] - 2
    elif case == 'nan': ticket['close_requested_at_ms'] = float('nan')
    elif case == 'adverse':
        f.loc[f.index[-1], 'open'] = price + (1 if side == 'LONG' else -1)*10
    before = copy.deepcopy(e.account.channel_profit_reentries)
    assert opposite_entry_releases(e.account, SYMBOL, f, price) == (case in ('valid', 'restart'))
    assert e.account.channel_profit_reentries == before


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('route', ['scan', 'fresh', 'cached', 'quote'])
@pytest.mark.parametrize('block', ['none', 'balance', 'minute', 'fresh_ck', 'execution_risk'])
async def test_release_reaches_real_order_gates(side, route, block, monkeypatch):
    e, f, price = setup(side)
    e._channel_chop_state = lambda *a: {'detected': False}
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    e._channel_profit_room = lambda *a: dict(allowed=True, checked=False)
    if block == 'balance': e.account.get_available_balance = lambda: 0.
    if block == 'execution_risk': e._execution_price_is_safe = AsyncMock(return_value=False)
    if block == 'minute': e._channel_candle_entry_blocked = lambda *a, **k: True
    if block == 'fresh_ck':
        fresh = f.copy()
        fresh['kc_middle'] = 100.
        e.fetch_klines = AsyncMock(return_value=fresh)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    if route == 'scan':
        await e._process_single_symbol(SYMBOL, 1., None, False)
    elif route == 'quote':
        e._channel_entry_quote_times = {SYMBOL: 1200.1}
        monkeypatch.setattr('core.engine.time.time', lambda: 1200.1)
        await e._try_live_pivot_entry(SYMBOL, f, price)
    else:
        signal = dict(side=side, entry_mode='CHANNEL_SWING', action='ENTER_MARKET', reason='new direction')
        snapshot = dict(frame=f, price=price, kc_upper=float(f.iloc[-1]['kc_upper']), kc_lower=float(f.iloc[-1]['kc_lower']))
        await e._place_structured_entry(SYMBOL, signal, price, channel_snapshot=snapshot if route == 'cached' else None)
    assert len(e.account.events) == int(block == 'none'), e.account.logs
    if e.account.events:
        assert e.account.events[0][2] == side
    assert not any('處理失敗' in message for message, _ in e.account.logs)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_diagnostics_preview_release_without_mutating(side):
    e, f, price = setup(side)
    e.is_running = True
    e._channel_entry_quote_times = {SYMBOL: 1200.1}
    e._channel_candle_entry_blocked = lambda *a: False
    before = copy.deepcopy(e.account.channel_profit_reentries)
    result = entry_diagnostics(e, SYMBOL, f, price, 1200.1)
    assert result['reason'] == 'KC_ENTRY_READY', result
    assert result['abnormal_ticket_release_ready']
    assert e.account.channel_profit_reentries == before
