import numpy as np
import pytest
from core.indicators import strict_pivot_type, detect_ma3_ma15_cross_and_turn
import pandas as pd


@pytest.mark.parametrize("values,index,expected", [
    ([1, 3, 2], 1, "PEAK_TURN"),
    ([3, 1, 2], 1, "TROUGH_TURN"),
    ([0, 3, 2, 1], 2, None),
    ([4, 1, 2, 3], 2, None),
    ([1, 3, 3, 2], 1, None),
    ([3, 1, 1, 2], 1, None),
    ([1, 3], 1, None),
    ([1, 3, 2, 4], 1, None),
    ([3, 1, 2, 0], 1, None),
    ([1, np.nan, 2], 1, None),
    ([1, 3, 2], -2, "PEAK_TURN"),
])
def test_confirmed_three_point_extrema(values, index, expected):
    assert strict_pivot_type(values, index) == expected


@pytest.mark.parametrize("tail", [[100, 103, 102, 101], [100, 97, 98, 99]])
def test_detector_does_not_label_monotonic_middle_as_pivot(tail):
    values = [100.] * 16 + tail
    frame = pd.DataFrame({"open": values, "close": values,
                          "high": np.array(values) + 1,
                          "low": np.array(values) - 1,
                          "ma3": values, "ma15": 100., "atr": 1.})
    result = detect_ma3_ma15_cross_and_turn(frame)
    # The turning point is at -3; the monotonic sample at -2 is not an extremum.
    assert result.get("pivot_offset") != -2 or not result.get("pivot_type")


from core.engine import TradingEngine
from test_channel_swing import _generate_macro_frame
from test_direct_break_execution import setup_engine, anyio_backend, confirm_break
from test_channel_swing_execution import SYMBOL


@pytest.mark.parametrize("side,points,action", [
    ("LONG", [100, 102, 101], "HOLD"),
    ("SHORT", [100, 98, 99], "HOLD"),
    ("LONG", [103, 102, 101], "HOLD"),
    ("SHORT", [97, 98, 99], "HOLD"),
    ("LONG", [100, 102, 102], "HOLD"),
    ("SHORT", [100, 98, 98], "HOLD"),
])
def test_geometric_pivot_without_favorable_impulse_keeps_position(side, points, action):
    frame = _generate_macro_frame()
    frame.loc[66:68, "ma3"] = points
    frame["kc_lower"], frame["kc_upper"] = (90., 101.) if side == "LONG" else (99., 110.)
    frame.loc[67, "close"] = 102. if side == "LONG" else 98.
    frame.loc[69, "open"] = 100.
    # A forming candle must never be the right-hand confirmation.
    frame.loc[69, "ma3"] = 50 if side == "LONG" else 150
    result = TradingEngine._channel_swing_action(frame, 100., side)
    assert result["action"] == action


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_scan_pivot_closes_without_reversing(setup_engine, side):
    engine, frame = setup_engine(side, side)
    frame.loc[66:68, "ma3"] = [100, 103, 101] if side == "LONG" else [100, 97, 99]
    # A real favorable waterfall and adverse long body accompany the MA3 turn.
    frame.loc[67, ['open', 'close']] = [100., 103.] if side == 'LONG' else [100., 97.]
    frame.loc[68, ['open', 'close']] = [103., 102.] if side == 'LONG' else [97., 98.]
    frame.loc[69, ['open', 'close']] = 102. if side == 'LONG' else 98.
    engine.tickers[SYMBOL] = float(frame.loc[69, 'close'])
    _, candidates = await engine._process_single_symbol(SYMBOL, 1., None, False)
    assert SYMBOL not in engine.account.positions
    assert len(engine.account.trades) == 1
    assert engine.account.trades[0]["status"] == "CLOSED"
    assert not candidates
    assert SYMBOL not in engine._channel_outer_reentry_after_exit


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("invalid", [None, "wick", "touch", "live_return"])
def test_live_body_crossing_alone_never_opens(side, invalid):
    frame = _generate_macro_frame()
    frame["kc_upper"], frame["kc_lower"], frame["atr"] = 102., 98., 1.
    frame.loc[69, "open"] = 100.
    price = 102.001 if side == "LONG" else 97.999
    if invalid == "wick":
        frame.loc[69, ["high", "low"]] = [105., 95.]
        price = 100.
    elif invalid == "touch": price = 102. if side == "LONG" else 98.
    elif invalid == "live_return": price = 100.
    result = TradingEngine._channel_swing_action(frame, price)
    assert result["action"] == "WAIT"
    assert result["side"] is None


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_scan_confirmed_break_opens_paper_position(setup_engine, side):
    engine, frame = setup_engine(side)
    confirm_break(frame, side)
    engine._channel_chop_state = lambda _: {"detected": False, "clear_direction": side}
    frame.loc[69, "open"] = 100.
    engine.tickers[SYMBOL] = float(frame.loc[68, "close"])
    await engine._process_single_symbol(SYMBOL, 1., None, False)
    assert engine.account.positions[SYMBOL]["side"] == side
    assert len(engine.account.trades) == 1
    assert engine.account.trades[0]["status"] == "OPEN"


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_failed_pivot_close_keeps_position_without_new_order(setup_engine, side):
    engine, frame = setup_engine(side, side)
    frame.loc[66:68, "ma3"] = [100, 103, 101] if side == "LONG" else [100, 97, 99]
    async def fail_close(*args, **kwargs):
        return False
    engine.account.close_position = fail_close
    # A real favorable waterfall and adverse long body accompany the MA3 turn.
    frame.loc[67, ['open', 'close']] = [100., 103.] if side == 'LONG' else [100., 97.]
    frame.loc[68, ['open', 'close']] = [103., 102.] if side == 'LONG' else [97., 98.]
    frame.loc[69, ['open', 'close']] = 102. if side == 'LONG' else 98.
    engine.tickers[SYMBOL] = float(frame.loc[69, 'close'])
    _, candidates = await engine._process_single_symbol(SYMBOL, 1., None, False)
    assert engine.account.positions[SYMBOL]["side"] == side
    assert not engine.account.trades
    assert not candidates
