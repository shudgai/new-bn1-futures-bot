import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_contract import (
    evaluate_continuation_entry,
    evaluate_entry_contract,
    entry_direction_problem,
)
from core.services import entry_contract
from core.services.entry_firewall import validate_account_entry


def live_frame(*, upper=103.0, ma15=102.2, confirmed_breakout=True):
    stamp = int(time.time() // 60) * 60_000
    rows = []
    for index, offset in enumerate((360_000, 300_000, 240_000, 180_000, 120_000, 60_000)):
        rows.append({
            "timestamp": stamp - offset,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.1,
            "volume": 10.0,
            "atr": 1.0,
            "ma5": 100.4 + 0.1 * index,
            "ma15": 99.5 + 0.1 * index,
            "kc_lower": 96.0 + 0.2 * index,
            "kc_middle": 99.0 + 0.2 * index,
            "kc_upper": 102.0 + 0.2 * index,
            "is_closed": True,
        })
    rows[-1].update(
        open=102.5 if confirmed_breakout else 100.0,
        high=103.4 if confirmed_breakout else 101.0,
        low=102.4 if confirmed_breakout else 99.0,
        close=103.3 if confirmed_breakout else 100.1,
    )
    rows.append({
        "timestamp": stamp,
        "open": 102.5,
        "high": 103.2,
        "low": 102.4,
        "close": 102.8,
        "atr": 1.0,
        "ma5": 102.6,
        "ma15": ma15,
        "kc_lower": 99.0,
        "kc_middle": 101.0,
        "kc_upper": upper,
        "channel_state": "UP",
        "kc_upper_slope": 0.2,
        "kc_lower_slope": 0.2,
        "is_closed": False,
    })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def short_live_frame(*, confirmed_breakout=True):
    stamp = int(time.time() // 60) * 60_000
    rows = []
    for index, offset in enumerate((360_000, 300_000, 240_000, 180_000, 120_000, 60_000)):
        rows.append({
            "timestamp": stamp - offset,
            "open": 100.0,
            "high": 100.2,
            "low": 99.8,
            "close": 99.9,
            "volume": 10.0,
            "atr": 1.0,
            "ma3": 100.0 - 0.2 * index,
            "ma5": 100.5 - 0.2 * index,
            "ma15": 101.0 - 0.1 * index,
            "kc_lower": 97.0 - 0.2 * index,
            "kc_middle": 100.0 - 0.2 * index,
            "kc_upper": 103.0 - 0.2 * index,
            "is_closed": True,
        })
    rows[-1].update(
        open=97.0 if confirmed_breakout else 100.0,
        high=97.2 if confirmed_breakout else 100.2,
        low=95.7 if confirmed_breakout else 99.8,
        close=95.8 if confirmed_breakout else 99.9,
    )
    rows.append({
        "timestamp": stamp,
        "open": 97.5,
        "high": 97.6,
        "low": 96.3,
        "close": 97.0,
        "atr": 1.0,
        "ma3": 97.4,
        "ma5": 97.4,
        "ma15": 97.8,
        "kc_lower": 96.9,
        "kc_middle": 99.7,
        "kc_upper": 102.7,
        "channel_state": "DOWN",
        "kc_upper_slope": -0.2,
        "kc_lower_slope": -0.2,
        "is_closed": False,
    })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def close_account(
    frame, *, side="LONG", symbol="CAP/USDT",
    status="CLOSED", close_offset_ms=1_000,
):
    stamp = float(frame.iloc[-1]["timestamp"])
    close_id = stamp - close_offset_ms
    return SimpleNamespace(
        positions={},
        trades=[{
            "id": close_id,
            "symbol": symbol,
            "action": f"CLOSE_{side}",
            "side": side,
            "status": status,
            "price": 97.1 if side == "SHORT" else 103.0,
            "reason": "Channel Swing PROFIT_PROTECTION test",
        }],
    ), close_id












def test_short_post_close_continuation_without_rail_break_is_rejected_before_revalidation():
    async def run():
        frame = short_live_frame()
        frame.attrs["entry_finality_verified"] = True
        account, _ = close_account(frame, side="SHORT")
        account.entry_frame_provider = AsyncMock(return_value=frame)
        decision = evaluate_entry_contract(
            frame, 97.0, account=account, symbol="CAP/USDT", diagnostics={},
        )
        assert decision is None
        account.entry_frame_provider.assert_not_awaited()

    asyncio.run(run())


def test_lobster_runner_does_not_send_post_short_close_without_rail_break():
    async def run():
        from core.services.symbol_runner import process_single_symbol_runner

        symbol = "龙虾/USDT"
        frame = short_live_frame()
        account, _ = close_account(frame, side="SHORT", symbol=symbol)
        account.log = Mock()
        engine = SimpleNamespace(
            account=account,
            strategy=SimpleNamespace(),
            tickers={symbol: 97.0},
            _execute_confirmed_channel_break=AsyncMock(return_value=True),
        )

        await process_single_symbol_runner(
            engine, symbol, time.time(), None, False,
            exit_frame=frame, exit_quote=97.0,
        )

        engine._execute_confirmed_channel_break.assert_not_awaited()

    asyncio.run(run())






@pytest.mark.parametrize(
    ("status", "close_offset_ms"),
    [("FAILED", 1_000), ("CLOSED", 61_000)],
)
def test_post_close_continuation_requires_filled_close_in_immediately_prior_bar(
    status, close_offset_ms,
):
    frame = live_frame()
    account, _ = close_account(
        frame, status=status, close_offset_ms=close_offset_ms,
    )

    assert evaluate_entry_contract(
        frame, 103.1, account=account, symbol="CAP/USDT",
    ) is None




