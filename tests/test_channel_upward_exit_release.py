"""Confirmed upward exits stop owning the entry route after a later valid green."""
import json
from unittest.mock import AsyncMock
import pytest
from test_channel_aligned_entry import aligned_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

def setup():
    f = aligned_frame('LONG')
    f['timestamp'] = [(i + 1) * 60_000 for i in range(len(f))]
    price = float(f.iloc[-1]['close'])
    e = _execution_engine(f, 'LONG', True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = price
    reason = 'Channel Swing KC_SHORT_LIVE_GREEN_LONG_EXIT'
    e.account.channel_profit_reentries = {SYMBOL: dict(
        token='upward', phase='closed', side='SHORT', mode='outer_cycle',
        requires_pullback=True, close_reason=reason,
        exit_bar_id=960000, close_requested_at_ms=960001)}
    e.account.trades = [dict(symbol=SYMBOL, action='CLOSE_SHORT', reason=reason, id=960002)]
    return e, f, price

@pytest.mark.parametrize('case', ['valid', 'restart', 'same_bar', 'live_only', 'red', 'doji',
                                  'small', 'nan', 'new_surge', 'closing', 'unmatched',
                                  'old_trade', 'missing_time', 'normal', 'held'])
def test_only_later_effective_green_releases_confirmed_upward_exit(case):
    e, f, price = setup()
    ticket = e.account.channel_profit_reentries[SYMBOL]
    if case == 'restart':
        e.account.channel_profit_reentries = json.loads(json.dumps(e.account.channel_profit_reentries))
    elif case == 'same_bar':
        ticket['exit_bar_id'] = f.iloc[-2]['timestamp']
        ticket['close_requested_at_ms'] = ticket['exit_bar_id'] + 1
        e.account.trades[0]['id'] = ticket['exit_bar_id'] + 2
    elif case in ('live_only', 'red', 'doji', 'small', 'nan'):
        for i in f.index[-4:-1]:
            close = f.loc[i, 'close']
            f.loc[i, 'open'] = (close + .01 if case == 'red' else
                               close - .001 if case == 'small' else
                               float('nan') if case == 'nan' else close)
    elif case == 'new_surge': price = 110.
    elif case == 'closing': ticket['phase'] = 'closing'
    elif case == 'unmatched': e.account.trades = []
    elif case == 'old_trade': e.account.trades[0]['id'] = 959999
    elif case == 'missing_time': ticket.pop('exit_bar_id')
    elif case == 'normal': ticket['requires_pullback'] = False
    elif case == 'held': e.account.positions[SYMBOL] = {'side': 'SHORT'}
    assert e._release_resolved_upward_exit(SYMBOL, f, price) is (case in ('valid', 'restart'))
    assert (SYMBOL in e.account.channel_profit_reentries) is (case not in ('valid', 'restart'))

@pytest.mark.anyio
@pytest.mark.parametrize('route', ['scan', 'fresh', 'cached'])
@pytest.mark.parametrize('block', ['none', 'fresh_invalid', 'room', 'balance', 'minute', 'pullback'])
async def test_release_enters_normal_route_without_bypassing_order_gates(route, block, monkeypatch):
    e, f, price = setup()
    e._channel_chop_state = lambda *a: {'detected': False}
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    e._channel_profit_room = lambda *a: dict(allowed=block != 'room', reason='room', checked=False)
    if block == 'balance': e.account.get_available_balance = lambda: 0.
    if block == 'minute': e._channel_candle_entry_blocked = lambda *a, **k: True
    if block == 'pullback': e._channel_intrabar_ready = lambda *a: False
    if block == 'fresh_invalid':
        fresh = f.copy()
        fresh.loc[fresh.index[-2], 'ma15'] = fresh.iloc[-3]['ma15']
        e.fetch_klines = AsyncMock(side_effect=[f, fresh] if route == 'scan' else [fresh])
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    if route == 'scan':
        await e._process_single_symbol(SYMBOL, 1., None, block == 'halt')
    else:
        signal = dict(side='LONG', entry_mode='CHANNEL_SWING', action='ENTER_MARKET', reason='released upward exit')
        snapshot = dict(frame=f, price=price, kc_upper=float(f.iloc[-1]['kc_upper']), kc_lower=float(f.iloc[-1]['kc_lower']))
        await e._place_structured_entry(SYMBOL, signal, price, channel_snapshot=snapshot if route == 'cached' else None)
    assert len(e.account.events) == int(block == 'none'), e.account.logs
    assert not any('處理失敗' in message for message, _ in e.account.logs)

@pytest.mark.anyio
async def test_release_does_not_bypass_daily_halt(monkeypatch):
    e, f, price = setup()
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    await e._process_single_symbol(SYMBOL, 1., None, True)
    assert not e.account.events

@pytest.mark.anyio
@pytest.mark.parametrize('cached', [False, True])
async def test_cached_signal_cannot_release_when_fresh_green_and_direction_are_invalid(cached, monkeypatch):
    e, f, price = setup()
    fresh = f.copy()
    fresh.loc[fresh.index[-4:-1], 'open'] = fresh.loc[fresh.index[-4:-1], 'close']
    # Neither the legacy green release nor the new opposite-CK release is ready.
    fresh['kc_middle'] = 100.
    e.fetch_klines = AsyncMock(return_value=fresh)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    signal = dict(side='LONG', entry_mode='CHANNEL_SWING', action='ENTER_MARKET', reason='stale green')
    snapshot = dict(frame=f, price=price, kc_upper=float(f.iloc[-1]['kc_upper']), kc_lower=float(f.iloc[-1]['kc_lower']))
    assert not await e._place_structured_entry(SYMBOL, signal, price, channel_snapshot=snapshot if cached else None)
    assert SYMBOL in e.account.channel_profit_reentries
    assert not e.account.events
