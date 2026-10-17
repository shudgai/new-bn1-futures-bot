import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.entry_firewall import (
    final_trend_alignment_problem,
    require_final_trend_alignment,
)


def trend_frame(direction, side):
    middles = [100.0, 101.0, 102.0] if direction == "UP" else [102.0, 101.0, 100.0]
    ma5, ma15 = (103.0, 102.0) if side == "LONG" else (99.0, 100.0)
    frame = pd.DataFrame([
        {
            "timestamp": (index + 1) * 60_000,
            "open": 100.0,
            "high": 104.0,
            "low": 96.0,
            "close": 101.0,
            "ma5": ma5,
            "ma15": ma15,
            "kc_middle": middle,
            "is_closed": True,
        }
        for index, middle in enumerate(middles)
    ])
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    return frame


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
def test_blocks_short_when_completed_kc_is_up(symbol):
    frame = trend_frame("UP", "SHORT")

    with pytest.raises(ValueError, match="BLOCKED_SHORT_KC_NOT_DOWN"):
        require_final_trend_alignment(frame, "SHORT", symbol)


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
def test_blocks_short_when_ma5_is_above_ma15(symbol):
    frame = trend_frame("DOWN", "LONG")

    with pytest.raises(ValueError, match="BLOCKED_SHORT_MA5_NOT_BELOW_MA15"):
        require_final_trend_alignment(frame, "SHORT", symbol)


@pytest.mark.parametrize(
    "direction,side",
    [("UP", "LONG"), ("DOWN", "SHORT")],
)
def test_allows_only_aligned_kc_and_ma_direction(direction, side):
    assert final_trend_alignment_problem(trend_frame(direction, side), side) is None


@pytest.mark.parametrize(
    "direction,side,expected",
    [
        ("DOWN", "LONG", "BLOCKED_LONG_KC_NOT_UP"),
        ("UP", "SHORT", "BLOCKED_SHORT_KC_NOT_DOWN"),
    ],
)
def test_blocks_side_opposed_to_kc_trend(direction, side, expected):
    assert final_trend_alignment_problem(trend_frame(direction, side), side) == expected


def test_blocks_missing_closed_candle_data():
    assert final_trend_alignment_problem(pd.DataFrame(), "SHORT") == (
        "BLOCKED_FINAL_TREND_DATA_INSUFFICIENT"
    )


def test_exchange_order_boundary_blocks_top_reversal_short_in_bull_trend():
    from core.testnet_account import BinanceTestnetAccount

    account = object.__new__(BinanceTestnetAccount)
    account.exchange = SimpleNamespace(create_order=AsyncMock())
    account.entry_frame_provider = AsyncMock(
        return_value=trend_frame("UP", "SHORT"),
    )
    context = {
        "entry_signal_code": "AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT",
        "entry_phase": "KC_TOP_WATERFALL_FLIP",
    }

    with pytest.raises(ValueError, match="BLOCKED_SHORT_KC_NOT_DOWN"):
        asyncio.run(account._send_order(
            "CAP/USDT",
            "market",
            "sell",
            1.0,
            entry_context=context,
        ))

    account.exchange.create_order.assert_not_awaited()
