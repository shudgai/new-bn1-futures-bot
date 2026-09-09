"""Middle trend plus live color, through real new-entry and reentry routes."""
import asyncio
from unittest.mock import AsyncMock
import pytest
from core.channel_outer_entry import middle_trend_entry
from core.engine import TradingEngine
from test_channel_swing_execution import _execution_engine, _narrow_channel_frame, SYMBOL


@pytest.fixture
def anyio_backend():
    return "asyncio"


def market(side):
    frame = _narrow_channel_frame()
    sign = 1 if side == "LONG" else -1
    frame["kc_upper"], frame["kc_lower"], frame["atr"] = 103., 97., 4.
    frame["kc_middle"] = 100.
    frame.loc[16:18, "kc_middle"] = [100. - sign * .2, 100. - sign * .1, 100.]
    # Both outside rails slope against their corresponding entry direction.
    frame.loc[16:18, "kc_upper"] = [103., 102.9, 102.8]
    frame.loc[16:18, "kc_lower"] = [97., 97.1, 97.2]
    price = 100. + sign * .4
    frame.loc[19, ["open", "close", "high", "low"]] = [100., price, max(100., price) + .1, min(100., price) - .1]
    return frame, price


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("case", ["valid", "opposite", "doji", "flat", "mixed", "reverse_trend", "missing", "invalid", "short", "live_trend", "no_ma", "stale_close"])
def test_middle_trend_signal(side, case):
    frame, price = market(side)
    if case == "opposite": frame.loc[19, "open"] = price + (1 if side == "LONG" else -1)
    if case == "doji": frame.loc[19, "open"] = price
    if case == "flat": frame.loc[16:18, "kc_middle"] = 100.
    if case == "mixed": frame.loc[16:18, "kc_middle"] = [100., 101., 100.]
    if case == "reverse_trend": frame.loc[16:18, "kc_middle"] = frame.loc[16:18, "kc_middle"].to_numpy()[::-1]
    if case == "missing": frame = frame.drop(columns="kc_middle")
    if case == "invalid": frame.loc[18, "kc_middle"] = float("nan")
    if case == "short": frame = frame.tail(3)
    if case == "live_trend": frame.loc[19, "kc_middle"] = 1. if side == "LONG" else 1000.
    if case == "no_ma": frame = frame.drop(columns=["ma3", "ma15"])
    if case == "stale_close": frame.loc[19, "close"] = 99. if side == "LONG" else 101.
    result = TradingEngine._channel_swing_action(frame, price)
    allowed = case in {"valid", "live_trend", "no_ma", "stale_close"}
    assert (result["action"] == "ENTER") == allowed, result
    if allowed: assert result["reason"] == "KC_MIDDLE_TREND_" + side


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reopen", [False, True])
@pytest.mark.parametrize("block", ["none", "room", "balance", "risk", "price", "halt", "changed_color", "changed_trend"])
async def test_order_and_reentry_safety(side, reopen, block, monkeypatch):
    frame, price = market(side)
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine.tickers[SYMBOL] = price
    engine._abnormal_market_entry_allowed = lambda *a, **k: block != "risk"
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    if block == "room": frame["atr"] = .001
    if block == "balance": engine.account.get_available_balance = lambda: 0.
    if block == "price": engine._execution_price_is_safe = AsyncMock(return_value=False)
    assert middle_trend_entry(frame, price)["side"] == side
    fresh = frame.copy()
    if block == "changed_color": fresh.loc[19, "open"] = price
    if block == "changed_trend": fresh.loc[16:18, "kc_middle"] = 100.
    engine.fetch_klines = AsyncMock(return_value=fresh)
    if reopen:
        engine.account.channel_profit_reentries = {SYMBOL: dict(side=side, phase="closed", token="middle", exit_bar_id=19)}
        await asyncio.gather(*(engine._try_profit_reentry(SYMBOL, frame, price, block == "halt") for _ in range(3)))
    else:
        await asyncio.gather(*(engine._execute_confirmed_channel_break(SYMBOL, frame, price, side, block == "halt") for _ in range(3)))
    assert len(engine.account.events) == (1 if block == "none" else 0), engine.account.logs
    if block == "none": assert engine.account.events[0][2] == side


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_scan_dispatches_middle_entry(side, monkeypatch):
    frame, price = market(side)
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine.tickers[SYMBOL] = price
    engine._abnormal_market_entry_allowed = lambda *a, **k: True
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", [SYMBOL])
    await engine._process_single_symbol(SYMBOL, 1., None, False)
    assert len(engine.account.events) == 1, engine.account.logs
    assert engine.account.events[0][2] == side
