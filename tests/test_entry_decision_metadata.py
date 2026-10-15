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
def test_three_bar_decision_reaches_account_and_deduplicates(monkeypatch, side):
    code = 'TRIGGER_A_KC_BREAKOUT'
    symbol = 'SUI/USDT'
    stamp = int(time.time() // 60) * 60000
    quote = 104. if side == 'LONG' else 96.
    rows = []
    for index in range(60):
        middle = 100. + (index * 0.001 * (1 if side == 'LONG' else -1))
        rows.append(dict(
            timestamp=stamp - (60-index) * 60000,
            open=100., close=100.1, high=101., low=99.,
            atr=1., ma3=100., ma5=100.4, ma15=99.,
            kc_middle=middle, kc_upper=middle+1., kc_lower=middle-1.,
            is_closed=True,
        ))
    if side == 'SHORT':
        for row in rows:
            for key in ('open', 'close', 'ma3', 'ma5', 'ma15'):
                row[key] = 200. - row[key]
            row['high'], row['low'] = 200. - row['low'], 200. - row['high']
            row['kc_middle'] = 200. - row['kc_middle']
            row['kc_upper'], row['kc_lower'] = (
                200. - row['kc_lower'], 200. - row['kc_upper'],
            )

    bars = [
        (100., 102., 100., 101.), (102., 103., 100.5, 101.5),
    ]
    for index, (opening, close, middle, upper) in zip((-2, -1), bars):
        lower = 2. * middle - upper
        if side == 'SHORT':
            opening, close, middle = (
                200. - opening, 200. - close, 200. - middle,
            )
            upper, lower = 200. - lower, 200. - upper
        rows[index].update(
            open=opening, close=close, high=max(opening, close)+0.1,
            low=min(opening, close)-0.1, atr=1.,
            ma3=close-0.25 if side == 'LONG' else close+0.25,
            ma5=close-0.5 if side == 'LONG' else close+0.5,
            ma15=close-1. if side == 'LONG' else close+1.,
            kc_middle=middle, kc_upper=upper, kc_lower=lower,
        )
    live_open = 102.
    live_middle, live_upper, live_lower = 102.5, 103.5, 101.5
    if side == 'SHORT':
        live_open, live_middle = 200. - live_open, 200. - live_middle
        live_upper, live_lower = 200. - live_lower, 200. - live_upper
    rows.append(dict(
        timestamp=stamp, open=live_open, close=quote,
        high=max(live_open, quote)+0.1, low=min(live_open, quote)-0.1,
        atr=1., ma3=quote-0.25 if side == 'LONG' else quote+0.25,
        ma5=quote-0.5 if side == 'LONG' else quote+0.5,
        ma15=quote-1. if side == 'LONG' else quote+1.,
        kc_middle=live_middle, kc_upper=live_upper, kc_lower=live_lower,
        is_closed=False,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs.update(
        timeframe_ms=60000, entry_finality_verified=True,
        entry_finality_server_ms=time.time()*1000,
    )
    monkeypatch.setattr(entry_contract, 'check_entry_gates',
                        lambda *args, **kwargs: (True, 'ENTRY_GATES_PASSED'))
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [symbol])
    monkeypatch.setattr('core.engine.is_entry_disabled', lambda _: False, raising=False)
    monkeypatch.setattr('core.config.is_entry_disabled', lambda _: False)
    monkeypatch.setattr('core.services.pre_entry_space_shadow.record_pre_entry_space_shadow',
                        lambda **kwargs: None)
    account = SimpleNamespace(
        positions={}, pending_limit_orders={}, trades=[], logs=[], log=Mock(),
        daily_loss_limit_hit=lambda: (False, 0.),
        get_wallet_balance=lambda: 100., get_available_balance=lambda: 100.,
    )
    assert entry_contract.evaluate_three_bar_outer_breakout(
        frame, quote, symbol=symbol, requested_side=side,
    ) is not None
    diagnostics = {}
    decision = entry_contract.evaluate_entry_contract(
        frame, quote, code, account=account, symbol=symbol,
        diagnostics=diagnostics,
    )
    assert decision is not None, diagnostics
    engine = object.__new__(TradingEngine)
    engine.account = account
    engine.tickers = {symbol: quote}
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    engine._abnormal_market_entry_allowed = Mock(return_value=True)
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args: 2.)
    signal = dict(
        side=side, candidate_bar_id=decision['confirmation_bar_id'],
        entry_mode='CHANNEL_SWING', signal_code=decision['type'],
        signal_id=decision['pending_signal_id'],
        _is_authorized=decision['_is_authorized'],
    )
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
    assert snapshot['closed_price'] == (103. if side == 'LONG' else 97.)
    assert snapshot['pair_confirmation_bar_id'] == stamp-60000
    assert snapshot['breakout_bar_id'] == stamp-120000
    assert captured['side'] == side
