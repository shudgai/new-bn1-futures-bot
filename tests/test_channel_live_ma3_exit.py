import json

import pytest
from core.engine import TradingEngine
from test_channel_ma3_outer_exit import fixture
from test_channel_swing_execution import _execution_engine, SYMBOL

pytestmark = pytest.mark.skip(reason='MA3-turn exit was removed from the active strategy')


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def setup(side):
    frame, position, price = fixture(side)
    frame['timestamp'] = [60_000 * (i + 1) for i in range(len(frame))]
    position.pop('entry_kc_upper')
    position.pop('entry_kc_lower')
    return frame, position, price


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['loss', 'profit', 'armed', 'flat', 'continuing',
                                  'invalid', 'missing_time', 'after_turn', 'same_bar_turn'])
def test_live_turn(side, case):
    frame, position, price = setup(side)
    sign = 1 if side == 'LONG' else -1
    if case == 'profit':
        position['entry_price'] = 100 - sign * 5
    elif case == 'armed':
        position['channel_profit_protection'] = {'armed': True}
    elif case == 'flat':
        price = 100 + sign * 2
    elif case == 'continuing':
        price = 100 + sign * 3
    elif case == 'invalid':
        frame.loc[18, 'close'] = float('nan')
    elif case == 'missing_time':
        frame = frame.drop(columns='timestamp')
    elif case in ('after_turn', 'same_bar_turn'):
        position['open_timestamp'] = 1201
        if case == 'same_bar_turn':
            assert not TradingEngine._channel_live_ma3_turn_exit(position, frame, 100 + sign * 3)
            position = json.loads(json.dumps(position))
    assert TradingEngine._channel_live_ma3_turn_exit(position, frame, price) is (
        case in ('loss', 'profit', 'armed', 'same_bar_turn'))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_intermediate_ma3_turn_does_not_exit(side):
    frame, position, price = setup(side)
    if side == 'LONG':
        frame.loc[17:18, 'close'] = [103.0, 104.0]
    else:
        frame.loc[17:18, 'close'] = [97.0, 96.0]
    assert not TradingEngine._channel_live_ma3_turn_exit(position, frame, price)


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('armed', [False, True])
@pytest.mark.parametrize('success', [False, True])
async def test_market_exit_and_persistent_retry(side, armed, success):
    frame, position, price = setup(side)
    engine = _execution_engine(frame, side, success)
    engine.account.save_state = lambda: None
    if armed:
        position['channel_profit_protection'] = {
            'identity': [side, position['open_timestamp'], position['entry_price'], position['qty']],
            'armed': True, 'peak_gross': 10,
        }
    engine.account.positions[SYMBOL].update(position)
    engine.tickers[SYMBOL] = price
    await engine._process_single_symbol(SYMBOL, 2., None, False)
    assert len(engine.account.events) == 1, engine.account.logs
    assert engine.account.events[0][2] == price
    assert engine.account.events[0][3].endswith(f'KC_{side}_LIVE_MA3_TURN_EXIT')
    assert (SYMBOL in engine.account.positions) is (not success)
    if not success:
        engine.account.positions[SYMBOL] = json.loads(json.dumps(engine.account.positions[SYMBOL]))
        frame['timestamp'] += 60_000
        engine.tickers[SYMBOL] = position['entry_price']
        await engine._process_single_symbol(SYMBOL, 3., None, False)
        assert len(engine.account.events) == 2, engine.account.logs
        assert all(event[0] == 'close' for event in engine.account.events)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('location', ['inside', 'touch', 'outside', 'invalid'])
@pytest.mark.parametrize('helper', ['_channel_live_ma3_turn_exit', '_channel_outer_ma3_turn_exit'])
def test_turn_exit_is_independent_of_ck_location(side, location, helper):
    frame, position, price = setup(side)
    position.update(entry_kc_upper=100.5, entry_kc_lower=99.5)
    sign = 1 if side == 'LONG' else -1
    key = 'kc_upper' if side == 'LONG' else 'kc_lower'
    pivot = 100 + sign * 3
    frame.loc[18, key] = pivot + sign * {'inside': 1, 'touch': 0, 'outside': -1, 'invalid': 0}[location]
    if location == 'invalid':
        frame.loc[18, key] = float('nan')
    # CK position and CK data no longer constrain a valid price/MA3 turn.
    assert getattr(TradingEngine, helper)(position, frame, price)
