"""Remaining upside and unarmed long middle-rail fallback."""
from unittest.mock import AsyncMock
import pytest
from core.engine import TradingEngine
from test_channel_swing_execution import _execution_engine, _narrow_channel_frame, SYMBOL

@pytest.fixture(autouse=True)
def fixed_costs(monkeypatch):
    monkeypatch.setattr('core.engine.TAKER_FEE_RATE', .0005)
    monkeypatch.setattr('core.engine.SLIPPAGE_PCT', .0001)
    monkeypatch.setattr('core.engine.NET_PROFIT_GUARANTEE_BUFFER', .006)

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.parametrize('price,allowed', [(100., True), (101.5, False), (102.1, False)])
def test_remaining_room_shrinks_as_entry_chases(price, allowed):
    f = _narrow_channel_frame(); f['atr'] = 2.
    result = TradingEngine._channel_long_profit_room(f, price)
    assert result['allowed'] is allowed
    assert result['target'] == 102.


def test_nearby_confirmed_peak_caps_upside():
    f = _narrow_channel_frame(); f['atr'] = 2.
    f.loc[15, 'high'] = 100.3
    result = TradingEngine._channel_long_profit_room(f, 100.)
    assert result['target'] == 100.3
    assert not result['allowed']


def test_live_atr_cannot_inflate_room_and_missing_data_blocks():
    f = _narrow_channel_frame(); f.loc[19, 'atr'] = 100.
    assert not TradingEngine._channel_long_profit_room(f, 100.)['allowed']
    f.loc[18, 'atr'] = float('nan')
    assert not TradingEngine._channel_long_profit_room(f, 100.)['allowed']


def test_declining_momentum_blocks_even_with_room(monkeypatch):
    f = _narrow_channel_frame(); f['atr'] = 2.
    monkeypatch.setattr(TradingEngine, '_channel_held_momentum_is_declining', staticmethod(lambda *a: True))
    result = TradingEngine._channel_long_profit_room(f, 100.)
    assert not result['allowed']
    assert result['reason'] == 'KC_LONG_MOMENTUM_FADING'


@pytest.mark.anyio
@pytest.mark.parametrize('token', [None, 'reopen'])
async def test_order_gate_rejects_no_room_before_open(monkeypatch, token):
    f = _narrow_channel_frame()
    e = _execution_engine(f, 'LONG', True); e.account.positions.clear()
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    e._fresh_channel_entry_snapshot = AsyncMock(return_value={
        'price': 100.3, 'kc_upper': 100.2, 'kc_lower': 99.8, 'frame': f})
    signal = {'side': 'LONG', 'entry_mode': 'CHANNEL_SWING', 'action': 'ENTER_MARKET',
              'profit_reentry_token': token}
    assert not await e._place_structured_entry(SYMBOL, signal, 100.3)
    assert not e.account.events
    assert any('KC_PROFIT_ROOM_INSUFFICIENT' in msg for msg, _ in e.account.logs)


@pytest.mark.anyio
@pytest.mark.parametrize('price,armed,closes', [(100., False, True), (99.9, False, True),
                                               (100.01, False, False), (100., True, False)])
@pytest.mark.parametrize('success', [True, False])
async def test_unarmed_long_middle_exit(price, armed, closes, success, monkeypatch):
    f = _narrow_channel_frame(); f['kc_upper'] = 102.; f['kc_lower'] = 98.
    e = _execution_engine(f, 'LONG', success)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL]['channel_profit_protection'] = {'armed': armed}
    e._channel_swing_action = lambda *a, **kw: {'action': 'HOLD', 'side': None}
    monkeypatch.setattr('core.engine.protection', lambda *a, **kw: None)
    e.tickers[SYMBOL] = price
    await e._process_single_symbol(SYMBOL, 1., None, False)
    assert len(e.account.events) == int(closes), e.account.logs
    assert (SYMBOL in e.account.positions) is (not closes or not success)
    if closes:
        assert e.account.events[0][3].endswith('KC_LONG_UNARMED_MIDDLE_EXIT')
        assert e.account.events[0][2] == price
    if closes and not success:
        await e._process_single_symbol(SYMBOL, 2., None, False)
        assert len(e.account.events) == 2


@pytest.mark.anyio
@pytest.mark.parametrize('success', [True, False])
async def test_losing_position_uses_real_protection_then_middle_exit(success):
    f = _narrow_channel_frame(); f['kc_upper'] = 102.; f['kc_lower'] = 98.
    e = _execution_engine(f, 'LONG', success)
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(entry_price=103., qty=1., open_timestamp=1.)
    e.tickers[SYMBOL] = 100.
    await e._process_single_symbol(SYMBOL, 2., None, False)
    assert len(e.account.events) == 1, e.account.logs
    assert e.account.events[0][3].endswith('KC_LONG_UNARMED_MIDDLE_EXIT')
    assert (SYMBOL in e.account.positions) is (not success)
