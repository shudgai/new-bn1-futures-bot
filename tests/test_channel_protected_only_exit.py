"""Unprotected holdings close only for post-entry adverse emergencies."""
import json
import pytest
from core.engine import TradingEngine
from test_channel_swing_execution import _execution_engine, _narrow_channel_frame, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

def market(side, case):
    f = _narrow_channel_frame()
    f['timestamp'] = [(i+1)*60000 for i in range(len(f))]
    f['atr'] = 1.
    f['kc_upper'], f['kc_lower'] = 110., 90.
    sign = 1 if side == 'SHORT' else -1
    price = 100. + sign * .1
    if case == 'waterfall': price = 100. + sign * 1.1
    if case == 'closed_waterfall': f.loc[18, 'close'] = 100. + sign * 1.1
    if case == 'double': f.loc[17:18, 'close'] = 100. + sign * .6
    if case == 'single': f.loc[18, 'close'] = 100. + sign * .6
    if case == 'small_pair': f.loc[17:18, 'close'] = 100. + sign * .4
    f.loc[19, 'close'] = price
    f['high'] = f[['open','close']].max(axis=1) + .1
    f['low'] = f[['open','close']].min(axis=1) - .1
    return f, price

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('case', ['waterfall','closed_waterfall','double','single','small_pair','normal'])
@pytest.mark.parametrize('success', [False, True])
async def test_emergency_only_and_failed_close_retries(side, case, success):
    f, price = market(side, case)
    e = _execution_engine(f, side, success)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(entry_price=100., qty=1., open_timestamp=1.)
    e.tickers[SYMBOL] = price
    await e._process_single_symbol(SYMBOL, 1., None, False)
    expected = case in ('waterfall','closed_waterfall','double')
    assert len(e.account.events) == int(expected), e.account.logs
    if expected:
        assert 'EMERGENCY_EXIT_' in e.account.events[0][3]
        assert (SYMBOL in e.account.positions) is (not success)
        if not success:
            e.account.positions[SYMBOL] = json.loads(json.dumps(e.account.positions[SYMBOL]))
            f['open'] = f['close'] = 100.
            f['timestamp'] += 60000
            e.tickers[SYMBOL] = 100.
            await e._process_single_symbol(SYMBOL, 2., None, False)
            assert len(e.account.events) == 2
            assert e.account.events[0][3] == e.account.events[1][3]

@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('case', ['double','closed_waterfall'])
def test_pre_entry_abnormal_candles_do_not_close_new_holding(side, case):
    f, price = market(side, case)
    pos = dict(side=side, entry_price=100., open_timestamp=1201.)
    assert TradingEngine._channel_exception_exit(pos, f, price) is None

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('action', ['EXIT','REVERSE'])
async def test_unarmed_ordinary_exit_or_reverse_does_not_close(side, action):
    f, price = market(side, 'normal')
    e = _execution_engine(f, side, True)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(entry_price=100., qty=1., open_timestamp=1.)
    e.tickers[SYMBOL] = price
    opposite = 'SHORT' if side == 'LONG' else 'LONG'
    e._channel_swing_action = lambda *a, **k: dict(action=action, side=opposite, reason='KC_LOWER_BREAKOUT' if opposite=='SHORT' else 'KC_UPPER_BREAKOUT')
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert not e.account.events
    assert not await e._execute_confirmed_channel_break(SYMBOL, f, price, opposite)
    assert not e.account.events
