import json

import pytest
from core.engine import TradingEngine
from test_channel_ma3_outer_exit import fixture
from test_channel_swing_execution import _execution_engine, SYMBOL




@pytest.fixture
def anyio_backend():
    return 'asyncio'


def setup(side):
    frame, position, price = fixture(side)
    frame['timestamp'] = [60_000 * (i + 1) for i in range(len(frame))]
    position.pop('entry_kc_upper')
    position.pop('entry_kc_lower')
    return frame, position, price


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('pending', [False, True])
async def test_legacy_pending_is_cleared_without_a_new_turn(side, pending):
    frame, position, price = setup(side)
    # Isolate a normal MA3 turn from the ATR emergency exceptions.
    frame['atr'] = 100.
    frame['close'] = price
    position['channel_live_ma3_exit_pending'] = pending
    engine = _execution_engine(frame, side, True)
    engine.account.save_state = lambda: None
    engine.account.positions[SYMBOL].update(position)
    engine.tickers[SYMBOL] = price
    await engine._process_single_symbol(SYMBOL, 2., None, False)
    assert not engine.account.events, engine.account.logs
    assert SYMBOL in engine.account.positions
    assert 'channel_live_ma3_exit_pending' not in engine.account.positions[SYMBOL]


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('already_armed', [False, True])
@pytest.mark.parametrize('triggered', [False, True])
async def test_ma3_turn_exits_even_when_protection_arms_or_is_already_armed(side, already_armed, triggered):
    frame, position, price = setup(side)
    frame['atr'] = 100.
    sign = 1 if side == 'LONG' else -1
    position['entry_price'] = price - sign * 5
    if already_armed:
        position['channel_profit_protection'] = {
            'identity': [side, position['open_timestamp'], position['entry_price'], position['qty']],
            'armed': True, 'peak_gross': 10 if triggered else 5,
        }
    engine = _execution_engine(frame, side, True)
    engine.account.save_state = lambda: None
    engine.account.positions[SYMBOL].update(position)
    engine.tickers[SYMBOL] = price
    await engine._process_single_symbol(SYMBOL, 2., None, False)
    assert len(engine.account.events) == 1
    assert engine.account.events[0][3].endswith('LIVE_MA3_TURN_EXIT')
    assert SYMBOL not in engine.account.positions
    assert not position.get('channel_live_ma3_exit_pending')


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_post_entry_ma3_turn_exits_at_live_price(side):
    frame, position, price = setup(side)
    frame['atr'] = 100.
    frame['ma3'] = float('nan')  # quote and closed prices are authoritative
    engine = _execution_engine(frame, side, True)
    engine.account.save_state = lambda: None
    engine.account.positions[SYMBOL].update(position)
    engine.tickers[SYMBOL] = price

    await engine._process_single_symbol(SYMBOL, 2., None, False)

    assert len(engine.account.events) == 1
    assert engine.account.events[0][2] == price
    assert engine.account.events[0][3].endswith('LIVE_MA3_TURN_EXIT')
