import asyncio
from unittest.mock import AsyncMock
import pytest
from core.channel_outer_entry import outside_reentry
from test_channel_middle_trend_entry import market
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'


def setup(side):
    f, _ = market(side)
    sign = 1 if side == 'LONG' else -1
    f['kc_upper'], f['kc_lower'] = 103., 97.
    price = 100 + sign * 4
    f.loc[19, ['open', 'close', 'high', 'low']] = [100 + sign * 3.5, price, max(price, 100), min(price, 100)]
    return f, price

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['valid', 'opposite', 'doji', 'ma_flat', 'ma_reverse', 'inside', 'ck_flat', 'invalid'])
def test_outer_cycle_conditions(side, case):
    f, price = setup(side)
    sign = 1 if side == 'LONG' else -1
    if case == 'opposite': f.loc[19, 'open'] = price + sign
    if case == 'doji': f.loc[19, 'open'] = price
    if case == 'ma_flat': f.loc[16, 'close'] = price
    if case == 'ma_reverse': f.loc[16, 'close'] = price + sign
    if case == 'inside': price = 100.
    if case == 'ck_flat': f['kc_middle'] = 100.
    if case == 'invalid': f.loc[18, 'close'] = float('nan')
    assert (outside_reentry(f, price, side).get('side') == side) is (case in ('valid', 'ck_flat'))

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('block', ['none', 'color', 'ma', 'halt', 'held', 'token', 'balance', 'risk', 'price', 'room', 'fading'])
async def test_reentry_fresh_validation_profit_room_and_dedup(side, block, monkeypatch):
    f, price = setup(side)
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.account.save_state = lambda: None
    e.tickers[SYMBOL] = price
    e._abnormal_market_entry_allowed = lambda *a, **k: block != 'risk'
    if block == 'balance': e.account.get_available_balance = lambda: 0.
    if block == 'price': e._execution_price_is_safe = AsyncMock(return_value=False)
    e._channel_profit_room = lambda *a, **k: {
        'allowed': block not in ('room', 'fading'), 'net_room_pct': 1., 'target': price,
        'reason': 'KC_PROFIT_ROOM_INSUFFICIENT' if block == 'room' else 'KC_' + side + '_MOMENTUM_FADING'}
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    ticket = dict(side=side, phase='closed', token='cycle', mode='outer_cycle', requires_pullback=True, exit_bar_id=17, pullback_bar=18)
    e.account.channel_profit_reentries = {SYMBOL: ticket}
    fresh = f.copy()
    if block == 'color': fresh.loc[19, 'open'] = price
    if block == 'ma': fresh.loc[16, 'close'] = price
    if block == 'held': e.account.positions[SYMBOL] = {'side': side}
    if block == 'token': ticket['phase'] = 'closing'
    e.fetch_klines = AsyncMock(return_value=fresh)
    await asyncio.gather(*(e._try_profit_reentry(SYMBOL, f, price, block == 'halt') for _ in range(3)))
    assert len(e.account.events) == (1 if block == 'none' else 0), e.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('success', [True, False])
async def test_ma3_exit_waits_for_recovery_before_reopening(side, success):
    from test_channel_live_ma3_exit import setup as turn_setup
    f, p, price = turn_setup(side)
    sign = 1 if side == 'LONG' else -1
    f['kc_middle'] = 100.
    f.loc[16:18, 'kc_middle'] = [100 - sign * .2, 100 - sign * .1, 100.]
    e = _execution_engine(f, side, success)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(p)
    e.tickers[SYMBOL] = price
    e._place_structured_entry = AsyncMock(return_value=True)
    await e._process_single_symbol(SYMBOL, 1., None, False)
    e._place_structured_entry.assert_not_awaited()
    if success:
        assert e.account.channel_profit_reentries[SYMBOL]['mode'] == 'outer_cycle'
        fresh, recovered = setup(side)
        fresh['timestamp'] = [60_000 * (i + 2) for i in range(len(fresh))]
        await e._try_profit_reentry(SYMBOL, fresh, recovered, False)
        e._place_structured_entry.assert_not_awaited()
        await e._try_profit_reentry(SYMBOL, fresh, 100., False)
        await e._try_profit_reentry(SYMBOL, fresh, recovered, False)
        e._place_structured_entry.assert_awaited_once()
    else:
        assert SYMBOL in e.account.positions
        assert SYMBOL not in e.account.channel_profit_reentries
