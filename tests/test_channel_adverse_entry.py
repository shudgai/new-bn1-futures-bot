"""Avoid entering a live candle that already satisfies the adverse exit."""
from unittest.mock import AsyncMock
import pytest
from core.services.strategies.outer_strategy import aligned_entry, aligned_entry_ready, outside_reentry, live_adverse_entry_safe
from core.engine import TradingEngine
from test_channel_sustained_trend import trend
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

def market(side, amount):
    f, price = trend(side)
    f['atr'] = 1.
    sign = 1 if side == 'LONG' else -1
    f.loc[f.index[-1], 'open'] = price + sign * amount
    f['high'] = f[['open', 'close']].max(axis=1) + .1
    f['low'] = f[['open', 'close']].min(axis=1) - .1
    return f, price

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('amount,allowed', [(-.1, True), (.1, True), (.49, True), (.5, False), (.6, False), (1.1, False)])
def test_shared_scan_reentry_gate(side, amount, allowed):
    f, price = market(side, amount)
    assert live_adverse_entry_safe(f, price, side) is allowed
    assert aligned_entry_ready(f, price, side) is allowed
    assert (aligned_entry(f, price)['action'] == 'ENTER') is allowed
    assert (outside_reentry(f, price, side)['action'] == 'ENTER') is allowed
    assert (TradingEngine._channel_swing_action(f, price)['action'] == 'ENTER') is allowed

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('invalid', [0., float('nan'), float('inf')])
def test_invalid_atr_is_not_safe(side, invalid):
    f, price = market(side, .1)
    f.loc[f.index[-2], 'atr'] = invalid
    assert not live_adverse_entry_safe(f, price, side)

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_quote_overrides_stale_close(side):
    f, price = market(side, .6)
    f.loc[f.index[-1], 'close'] = f.iloc[-1]['open']
    assert not live_adverse_entry_safe(f, price, side)
    assert live_adverse_entry_safe(f, float(f.iloc[-1]['open']), side)

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('reentry', [False, True])
async def test_order_cannot_bypass_live_abnormality(side, cached, reentry, monkeypatch):
    f, price = market(side, .6)
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    snapshot = dict(frame=f, price=price, kc_upper=float(f.iloc[-1]['kc_upper']), kc_lower=float(f.iloc[-1]['kc_lower']))
    e._fresh_channel_entry_snapshot = AsyncMock(return_value=snapshot)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    signal = dict(side=side, entry_mode='CHANNEL_SWING', action='ENTER_MARKET', signal_code='KC_CONTINUATION_' + side)
    if reentry:
        signal['profit_reentry_token'] = 'closed-profit'
    assert not await e._place_structured_entry(SYMBOL, signal, price, channel_snapshot=snapshot if cached else None)
    assert not e.account.events

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_new_quote_invalidates_previously_safe_snapshot(side, monkeypatch):
    f, old_price = market(side, .1)
    sign = 1 if side == 'LONG' else -1
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.tickers[SYMBOL] = old_price - sign * .6
    snapshot = dict(frame=f, price=old_price, kc_upper=float(f.iloc[-1]['kc_upper']), kc_lower=float(f.iloc[-1]['kc_lower']))
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    signal = dict(side=side, entry_mode='CHANNEL_SWING', action='ENTER_MARKET', signal_code='KC_CONTINUATION_' + side)
    assert aligned_entry_ready(f, old_price, side)
    assert not await e._place_structured_entry(SYMBOL, signal, old_price, channel_snapshot=snapshot)
    assert not e.account.events
    assert any('KC_LIVE_ADVERSE_ENTRY_WAIT' in str(log) for log in e.account.logs)
