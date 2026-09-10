import json
from unittest.mock import AsyncMock
import pytest
from core.channel_outer_entry import abnormal_pullback_ready
from test_channel_outer_cycle import setup
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_pullback_must_follow_exit_and_survive_restart(side):
    f, price = setup(side)
    ticket = dict(side=side, exit_bar_id=19)
    assert not abnormal_pullback_ready(ticket, f, 100.)
    assert 'pullback_bar' not in ticket
    f.index += 1
    assert not abnormal_pullback_ready(ticket, f, price)
    assert not abnormal_pullback_ready(ticket, f, 100.)
    ticket = json.loads(json.dumps(ticket))
    assert abnormal_pullback_ready(ticket, f, price)
    invalid = f.copy()
    invalid.loc[20, 'kc_upper'] = float('nan')
    assert not abnormal_pullback_ready(ticket, invalid, price)

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('success', [True, False])
async def test_abnormal_exit_creates_wait_only_after_success(side, success):
    f, price = setup(side)
    e = _execution_engine(f, side, success)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(entry_price=100., qty=1., open_timestamp=1.)
    e.tickers[SYMBOL] = price
    reason = 'KC_LONG_LIVE_RED_LONG_EXIT' if side == 'LONG' else 'KC_SHORT_LIVE_GREEN_LONG_EXIT'
    e._channel_swing_action = lambda *a, **k: dict(action='EXIT', side=None, reason=reason)
    e._place_structured_entry = AsyncMock(return_value=True)
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert len(e.account.events) == 1, e.account.logs
    e._place_structured_entry.assert_not_awaited()
    if success:
        ticket = e.account.channel_profit_reentries[SYMBOL]
        assert ticket['phase'] == 'closed'
        assert ticket['requires_pullback']
        await e._try_profit_reentry(SYMBOL, f, price, False)
        e._place_structured_entry.assert_not_awaited()
        f.index += 1
        await e._try_profit_reentry(SYMBOL, f, 100., False)
        e._place_structured_entry.assert_not_awaited()
        await e._try_profit_reentry(SYMBOL, f, price, False)
        e._place_structured_entry.assert_awaited_once()
    else:
        assert SYMBOL in e.account.positions
        assert SYMBOL not in e.account.channel_profit_reentries

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('block', ['none', 'color', 'ma', 'inside', 'halt', 'balance', 'room'])
async def test_pullback_reentry_revalidates_and_preserves_risk(side, block, monkeypatch):
    f, price = setup(side)
    f.index += 2
    # Successful recovery still needs space beyond the reentry price.
    f['atr'] = 6.
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = price
    e.account.channel_profit_reentries = {SYMBOL: dict(side=side, token='a', phase='closed', mode='outer_cycle',
                                                      requires_pullback=True, exit_bar_id=19, pullback_bar=20)}
    fresh = f.copy()
    if block == 'room': fresh['atr'] = 4.
    if block == 'color': fresh.loc[21, 'open'] = price
    if block == 'ma': fresh.loc[18, 'close'] = price
    if block == 'inside': fresh['kc_upper'], fresh['kc_lower'] = 110., 90.
    if block == 'balance': e.account.get_available_balance = lambda: 0.
    e.fetch_klines = AsyncMock(return_value=fresh)
    e._abnormal_market_entry_allowed = lambda *a, **k: True
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    await e._try_profit_reentry(SYMBOL, f, price, block == 'halt')
    assert len(e.account.events) == int(block == 'none'), e.account.logs

@pytest.mark.anyio
@pytest.mark.parametrize('matched', [True, False, 'old'])
async def test_abnormal_closing_ticket_needs_matching_trade_after_restart(matched):
    f, price = setup('LONG')
    e = _execution_engine(f, 'LONG', True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    reason = 'Channel Swing KC_LONG_LIVE_RED_LONG_EXIT'
    e.account.channel_profit_reentries = {SYMBOL: dict(side='LONG', token='a', phase='closing', mode='outer_cycle',
                                                      requires_pullback=True, exit_bar_id=19, close_reason=reason, close_requested_at_ms=1000)}
    e.account.trades = [dict(id=999 if matched == 'old' else 1001, symbol=SYMBOL, action='CLOSE_LONG', reason=reason)] if matched else []
    e._place_structured_entry = AsyncMock()
    await e._try_profit_reentry(SYMBOL, f, price, False)
    assert (SYMBOL in e.account.channel_profit_reentries) is (matched is True)
    e._place_structured_entry.assert_not_awaited()
