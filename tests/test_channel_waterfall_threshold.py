"""The standalone waterfall needs 1.5 ATR; other exit and entry gates retain scope."""
import json
import pytest
from core.engine import TradingEngine
from core.services.strategies.outer_strategy import live_adverse_entry_safe
from test_channel_protected_only_exit import market
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('closed', [False, True])
@pytest.mark.parametrize('amount', [1., 1.15, 1.499, 1.5, 1.6])
def test_live_and_closed_waterfall_boundary(side, closed, amount):
    f, _ = market(side, 'normal')
    sign = -1 if side == 'LONG' else 1
    price = 100 + sign * amount
    if closed:
        f.loc[f.index[-2], 'close'] = price
        price = 100.
    # The unfinished bar ATR and shadows cannot change the threshold.
    f.loc[f.index[-1], 'atr'] = 100.
    f['low'], f['high'] = 1., 200.
    p = dict(side=side, entry_price=100., open_timestamp=1.)
    reason = TradingEngine._channel_exception_exit(p, f, price)
    expected = 'EMERGENCY_EXIT_' + ('CLOSED' if closed else 'LIVE') + '_ADVERSE_WATERFALL'
    assert reason == (expected if amount >= 1.5 else None)

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_double_abnormal_and_entry_filter_keep_half_atr(side):
    f, _ = market(side, 'normal')
    sign = -1 if side == 'LONG' else 1
    f.loc[f.index[-3:-1], 'close'] = 100 + sign * .5
    p = dict(side=side, entry_price=100., open_timestamp=1.)
    assert TradingEngine._channel_exception_exit(p, f, 100.) == 'EMERGENCY_EXIT_2_CANDLE_ADVERSE'
    assert not live_adverse_entry_safe(f, 100 + sign * .5, side)
    assert live_adverse_entry_safe(f, 100 + sign * .49, side)

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('route', ['quote', 'scan'])
@pytest.mark.parametrize('success', [False, True])
async def test_order_threshold_and_failed_close_retry(side, route, success, monkeypatch):
    f, _ = market(side, 'normal')
    e = _execution_engine(f, side, success)
    e.is_running = True
    e.account.save_state = lambda: None
    # Same-bar position: the full original candle body is used.
    e.account.positions[SYMBOL].update(entry_price=100., qty=1., open_timestamp=1201.)
    e._channel_exit_frames = {SYMBOL: f}
    monkeypatch.setattr('core.engine.time.time', lambda: 1201.)
    sign = -1 if side == 'LONG' else 1
    async def tick(price):
        e.tickers[SYMBOL] = price
        if route == 'quote':
            await e._channel_quote_exit(SYMBOL, price, 1201000)
        else:
            await e._process_single_symbol(SYMBOL, 1201., None, False)
    await tick(100 + sign * 1.15)
    assert not e.account.events, e.account.logs
    await tick(100 + sign * 1.5)
    assert len(e.account.events) == 1, e.account.logs
    assert e.account.events[0][0] == 'close'
    assert e.account.events[0][3].endswith('EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL')
    if success:
        assert SYMBOL not in e.account.positions
        assert e.account.channel_profit_reentries[SYMBOL]['requires_pullback']
    else:
        e.account.positions[SYMBOL] = json.loads(json.dumps(e.account.positions[SYMBOL]))
        await tick(100.)
        assert len(e.account.events) == 2
        assert e.account.events[0][3] == e.account.events[1][3]

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_existing_pending_exit_still_retries(side):
    f, price = market(side, 'normal')
    reason = 'EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL'
    p = dict(side=side, entry_price=100., open_timestamp=1., channel_exception_exit_pending=reason)
    assert TradingEngine._channel_exception_exit(p, f, price) == reason

def test_lobster_observed_body_is_below_new_threshold():
    f, _ = market('LONG', 'normal')
    f['open'] = f['close'] = .038023
    f['atr'] = .0001508
    p = dict(side='LONG', entry_price=.0380078004, open_timestamp=1.)
    assert TradingEngine._channel_exception_exit(p, f, .037849) is None
