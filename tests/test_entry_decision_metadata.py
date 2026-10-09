"""A valid pipeline decision must survive execution and account revalidation."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services import entry_contract
from core.services.entry_firewall import validate_account_entry


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('code', [
    'TRIGGER_A_KC_BREAKOUT', 'TRIGGER_B_MA_CROSS',
    'TRIGGER_C_CONTINUATION', 'RE_ENTRY',
])
def test_pipeline_decision_reaches_account_and_deduplicates(monkeypatch, side, code):
    # Arrange: isolate signal qualification while exercising real metadata consumers.
    if code == 'RE_ENTRY':
        code += '_' + side
    symbol = 'SUI/USDT'
    stamp = int(time.time() // 60) * 60000
    quote = 102. if side == 'LONG' else 98.
    frame = pd.DataFrame([
        dict(timestamp=stamp - (3-i)*60000, open=100., close=quote,
             high=103., low=97., kc_upper=101., kc_lower=99., atr=1.,
             is_closed=i < 3)
        for i in range(4)
    ])
    frame.attrs['entry_finality_verified'] = True
    monkeypatch.setattr(entry_contract, 'detect_raw_triggers',
                        lambda *args: (side, code))
    monkeypatch.setattr(entry_contract, 'check_entry_gates',
                        lambda *args: (True, 'ENTRY_GATES_PASSED'))
    monkeypatch.setattr(entry_contract, 'validate_strict_entry',
                        lambda *args: (True, 'STRICT_ENTRY_GATES_PASSED', {'passed': True}))
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [symbol])
    monkeypatch.setattr('core.engine.is_entry_disabled', lambda _: False, raising=False)
    monkeypatch.setattr('core.config.is_entry_disabled', lambda _: False)
    monkeypatch.setattr('core.services.pre_entry_space_shadow.record_pre_entry_space_shadow',
                        lambda **kwargs: None)
    account = SimpleNamespace(
        positions={}, pending_limit_orders={}, trades=[], logs=[], log=Mock(),
        get_wallet_balance=lambda: 100., get_available_balance=lambda: 100.,
    )
    engine = object.__new__(TradingEngine)
    engine.account = account
    engine.tickers = {symbol: quote}
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    engine._abnormal_market_entry_allowed = Mock(return_value=True)
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args: 2.)
    signal = dict(side=side, candidate_bar_id=stamp-60000,
                  entry_mode='CHANNEL_SWING', signal_code=code)
    captured = {}

    async def open_position(**kwargs):
        captured.update(kwargs)
        decision = await validate_account_entry(
            account, symbol, side, kwargs['entry_context'])
        account.trades.append(dict(symbol=symbol, action='OPEN_'+side,
                                   channel_confirmation_bar_id=decision['confirmation_bar_id']))
        return True

    account.open_position = AsyncMock(side_effect=open_position)

    # Act: run execution, firewall and a duplicate attempt in one event loop.
    async def run():
        assert await engine._place_structured_entry(symbol, signal, quote)
        assert not await engine._place_structured_entry(symbol, signal, quote)

    asyncio.run(run())

    # Assert: all required metadata reaches account safety without optional exit data.
    account.open_position.assert_awaited_once()
    snapshot = captured['entry_context']['entry_snapshot']
    assert snapshot['closed_price'] == quote
    assert snapshot['pair_confirmation_bar_id'] == stamp-60000
    assert snapshot['breakout_bar_id'] == stamp-60000
    assert captured['side'] == side
