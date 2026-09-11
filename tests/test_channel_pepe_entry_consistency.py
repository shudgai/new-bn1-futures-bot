"""Third candle color cannot override a valid closed breakout and live MA3."""
from unittest.mock import AsyncMock
import pytest
from core.services.strategies.outer_strategy import aligned_entry, aligned_entry_ready, outside_reentry
from core.engine import TradingEngine
from channel_test_frames import closed_outer_entry_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("offset", [0., .1])
def test_third_candle_opposite_or_doji_keeps_closed_breakout(side, offset):
    f = closed_outer_entry_frame(side)
    sign = 1 if side == "LONG" else -1
    price = float(f.iloc[-1]["close"])
    f.loc[f.index[-1], "open"] = price + sign * offset
    f["high"] = f[["open", "close"]].max(axis=1) + .2
    f["low"] = f[["open", "close"]].min(axis=1) - .2
    assert aligned_entry_ready(f, price, side)
    assert aligned_entry(f, price)["side"] == side
    assert outside_reentry(f, price, side)["side"] == side
    assert TradingEngine._channel_swing_action(f, price)["action"] == "ENTER"
    recovered = price + sign * (offset + .05)
    assert aligned_entry(f, recovered)["side"] == side
    assert aligned_entry_ready(f, recovered, side)

@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_order_uses_same_indicator_history_as_scan(side):
    f = closed_outer_entry_frame(side)
    e = _execution_engine(f, side, True)
    e.account.positions.clear()
    e.tickers[SYMBOL] = float(f.iloc[-1]["close"])
    e.fetch_klines = AsyncMock(return_value=f)
    assert await e._fresh_channel_entry_snapshot(SYMBOL, side, e._channel_candidate_bar_id(f))
    assert e.fetch_klines.call_args.kwargs["limit"] == 200
