"""A fresh, flat engine must wait for a closed outer breakout before entry."""
import pytest

from core.engine import TradingEngine
from test_direct_break_execution import setup_engine, confirm_break
from test_channel_swing_execution import SYMBOL


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("invalid", ["live_only", "already_outside", "old_break", "inside_confirmation"])
async def test_fresh_flat_scan_requires_latest_closed_break(setup_engine, side, invalid):
    engine, frame = setup_engine(side)
    confirm_break(frame, side)
    valid = frame.copy()
    if invalid == "live_only":
        frame.loc[67:68, ["open", "close"]] = 100.
        frame.loc[69, "open"] = 100.
    elif invalid == "already_outside":
        frame.loc[67, "open"] = 102.5 if side == "LONG" else 97.5
    elif invalid == "old_break":
        frame.loc[65] = valid.loc[67].copy()
        frame.loc[66] = valid.loc[68].copy()
        frame.loc[67:68, ["open", "close"]] = 100.
    else:
        frame.loc[68, "close"] = 100.
    # Even a legacy peak signal must not override the outer-break decision.
    engine._channel_peak_reversal_action = lambda *_: {
        "action": "ENTER", "side": side, "reason": "PEAK_REVERSAL_SHORT",
    }
    await engine._process_single_symbol(SYMBOL, 1., None, False)
    assert not engine.account.positions
    assert not engine.account.trades
    assert not await engine._execute_confirmed_channel_break(
        SYMBOL, frame, engine.tickers[SYMBOL], side,
    )
    frame.loc[:] = valid
    await engine._process_single_symbol(SYMBOL, 2., None, False)
    assert engine.account.positions[SYMBOL]["side"] == side
    assert "處理失敗" not in str(engine.account.logs)


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("location", ["inside", "touch", "opposite", "outside"])
async def test_order_rechecks_price_against_current_outer_rail(setup_engine, side, location):
    e, f = setup_engine(side)
    confirm_break(f, side)
    edge = 102. if side == "LONG" else 98.
    price = {"inside": 100., "touch": edge,
             "opposite": 97. if side == "LONG" else 103.,
             "outside": 103. if side == "LONG" else 97.}[location]
    e.tickers[SYMBOL] = price
    result = await e._execute_confirmed_channel_break(SYMBOL, f, price, side)
    assert result is (location == "outside")
    assert bool(e.account.positions) is (location == "outside")
    assert len(e.account.trades) == (1 if location == "outside" else 0)
