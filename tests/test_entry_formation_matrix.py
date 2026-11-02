import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock
from core.engine import TradingEngine
import core.testnet_account as testnet_account_module


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(
        testnet_account_module,
        "STATE_FILE",
        str(tmp_path / "testnet_account.json"),
    )
    instance = TradingEngine()
    instance.account = MagicMock()
    instance.account.positions = {}
    instance.account.trades = []
    instance.account.open_position = AsyncMock(return_value=True)
    instance.tickers = {"1000PEPE/USDT": 13}
    return instance


def make_frame(rows):
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    for column in (
        "open", "high", "low", "close", "kc_upper",
        "kc_middle", "kc_lower", "atr",
    ):
        if column in frame:
            frame[column] = frame[column].astype(float)
    return frame


@pytest.mark.parametrize("symbol", ["1000PEPE/USDT", "龙虾/USDT"])
@pytest.mark.anyio
async def test_two_bar_formation_rejects_weak_body_rail_and_gap(engine, symbol):
    rows = [
        {
            "timestamp": 1_000_000, "open": 10, "high": 12, "low": 9,
            "close": 10.1, "is_closed": True, "kc_upper": 10.5,
            "kc_middle": 10, "kc_lower": 9.5, "atr": 1,
        },
        {
            "timestamp": 1_060_000, "open": 10.1, "high": 13, "low": 10,
            "close": 12, "is_closed": True, "kc_upper": 11.5,
            "kc_middle": 11, "kc_lower": 10.5, "atr": 1,
        },
        {
            "timestamp": 1_120_000, "open": 12, "high": 13, "low": 10.5,
            "close": 12.5, "is_closed": False, "kc_upper": 12.5,
            "kc_middle": 12, "kc_lower": 11.5, "atr": 1,
        },
    ]

    frame = make_frame(rows)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    assert await engine._fresh_channel_entry_snapshot(symbol, "LONG") is None, (
        "K1 body < 20% should be blocked"
    )

    rows[0]["close"] = 11
    rows[1]["close"] = 10.2
    frame = make_frame(rows)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    assert await engine._fresh_channel_entry_snapshot(symbol, "LONG") is None, (
        "K2 body < 20% should be blocked"
    )

    rows[1]["close"] = 11.4
    frame = make_frame(rows)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    assert await engine._fresh_channel_entry_snapshot(symbol, "LONG") is None, (
        "K2 not outside KC should be blocked"
    )

    rows[1]["close"] = 12
    rows[1]["timestamp"] = 1_120_000
    rows[2]["timestamp"] = 1_180_000
    frame = make_frame(rows)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    assert await engine._fresh_channel_entry_snapshot(symbol, "LONG") is None, (
        "Non-adjacent K1/K2 should be blocked"
    )
