"""MA15 rail-adjacent V exits use closed bars and support either signal order."""
import pandas as pd
import pytest

from core.services.strategies.outer_strategy import (
    ma15_rail_pivot_exit_ready, ma15_unarmed_opposite_exit_ready,
    opposite_outer_breakout_side, three_point_pivot_exit_ready,
)
from core.engine import TradingEngine


def frame_for(side, ma15_values, rail_offsets):
    rows = []
    for index, (ma15, offset) in enumerate(zip(ma15_values, rail_offsets)):
        if side == "SHORT":
            lower = ma15 - offset
            upper = lower + 10.0
        else:
            upper = ma15 + offset
            lower = upper - 10.0
        rows.append({
            "open": 100.0 if side == "LONG" else 99.0,
            "high": 102.0 if index == 1 and side == "LONG" else 101.0,
            "low": 98.0 if index == 1 and side == "SHORT" else 99.0,
            "close": 99.0 if index == 2 and side == "LONG" else 101.0 if index == 2 else 100.0,
            "ma15": ma15,
            "kc_upper": upper,
            "kc_lower": lower,
            "timestamp": index + 1,
        })
    rows.append(rows[-1].copy())
    rows[-1]["timestamp"] += 1
    return pd.DataFrame(rows)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ma15_rail_pivot_requires_the_turning_point_to_remain_near_the_rail(side):
    values = [100.0, 99.0, 100.0] if side == "SHORT" else [100.0, 101.0, 100.0]
    assert ma15_rail_pivot_exit_ready(frame_for(side, values, [4.5, 1.0, 3.0]), side)
    assert not ma15_rail_pivot_exit_ready(frame_for(side, values, [1.0, 4.5, 4.5]), side)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ma15_rail_pivot_rejects_unconfirmed_or_distant_turn(side):
    values = [100.0, 99.0, 100.0] if side == "SHORT" else [100.0, 101.0, 100.0]
    monotonic = [100.0, 99.0, 98.0] if side == "SHORT" else [100.0, 101.0, 102.0]
    assert not ma15_rail_pivot_exit_ready(frame_for(side, values, [4.5, 4.2, 4.5]), side)
    assert not ma15_rail_pivot_exit_ready(frame_for(side, monotonic, [1.0, 1.0, 1.0]), side)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ma15_rail_pivot_rejects_price_fakeout_without_three_point_pivot(side):
    values = [100.0, 99.0, 100.0] if side == "SHORT" else [100.0, 101.0, 100.0]
    frame = frame_for(side, values, [4.5, 1.0, 3.0])
    if side == "LONG":
        frame.loc[2, "high"] = frame.loc[1, "high"]
    else:
        frame.loc[2, "low"] = frame.loc[1, "low"]
    assert not ma15_rail_pivot_exit_ready(frame, side)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_three_point_exit_does_not_require_ma15_to_touch_the_rail(side):
    values = [100.0, 99.0, 100.0] if side == "SHORT" else [100.0, 101.0, 100.0]
    frame = frame_for(side, values, [4.5, 4.5, 4.5])
    frame["ma3"] = [100.0, 99.0, 100.0, 100.0] if side == "SHORT" else [100.0, 101.0, 100.0, 100.0]
    frame["kc_middle"] = 100.
    assert three_point_pivot_exit_ready(frame, side)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_three_point_exit_rejects_price_or_ma3_fakeout(side):
    values = [100.0, 99.0, 100.0] if side == "SHORT" else [100.0, 101.0, 100.0]
    frame = frame_for(side, values, [4.5, 4.5, 4.5])
    frame["ma3"] = [100.0, 99.0, 100.0, 100.0] if side == "SHORT" else [100.0, 101.0, 100.0, 100.0]
    if side == "LONG":
        frame.loc[2, "high"] = frame.loc[1, "high"]
    else:
        frame.loc[2, "low"] = frame.loc[1, "low"]
    assert not three_point_pivot_exit_ready(frame, side)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_unarmed_ma15_trend_exits_when_ma3_ma15_and_kc_reverse(side):
    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
         "ma3": 100.0, "ma15": 100.0, "kc_upper": 110.0, "kc_lower": 90.0,
         "kc_middle": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
         "ma3": 100.0, "ma15": 99.0 if side == "SHORT" else 101.0,
         "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 99.0 if side == "SHORT" else 101.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
         "ma3": 100.0, "ma15": 100.0,
         "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0},
        {"open": 100.0, "high": 104.0, "low": 96.0, "close": 100.0,
         "ma3": 102.0 if side == "SHORT" else 98.0,
         "ma15": 101.0 if side == "SHORT" else 99.0,
         "kc_upper": 110.0, "kc_lower": 90.0, "kc_middle": 100.0},
    ]
    price = 103.0 if side == "SHORT" else 97.0
    assert ma15_unarmed_opposite_exit_ready(pd.DataFrame(rows), price, side)


def test_mid_trend_exit_waits_for_two_closed_bodies_then_allows_breakout():
    rows = [
        {"open": 101.0, "high": 102.0, "low": 99.0, "close": 100.0,
         "ma3": 100.0, "ma15": 101.0, "kc_upper": 105.0, "kc_lower": 95.0,
         "kc_middle": 101.0, "timestamp": 60_000},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
         "ma3": 100.0, "ma15": 101.0, "kc_upper": 105.0, "kc_lower": 95.0,
         "kc_middle": 101.0, "timestamp": 120_000},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
         "ma3": 100.0, "ma15": 100.0, "kc_upper": 105.0, "kc_lower": 95.0,
         "kc_middle": 100.0, "timestamp": 180_000},
        {"open": 100.0, "high": 101.0, "low": 98.0, "close": 99.0,
         "ma3": 99.0, "ma15": 99.5, "kc_upper": 105.0, "kc_lower": 95.0,
         "kc_middle": 100.0, "timestamp": 240_000},
    ]
    frame = pd.DataFrame(rows)
    exit_info = {"side": "SHORT", "exit_bar_id": 60_000, "require_new_closed_break": True}
    gate = TradingEngine._channel_peak_exit_reentry_blocked
    assert gate("ENTER", False, "SHORT", frame, exit_info, "TEST", live_price=99.0)
    frame.loc[1, ["open", "high", "low", "close"]] = [100.0, 101.0, 93.0, 94.0]
    frame.loc[2, ["open", "high", "low", "close"]] = [94.0, 95.0, 92.0, 93.0]
    frame.loc[1:2, "ma3"] = [99., 97.]
    frame["atr"] = 2.
    frame.loc[3, ["open", "high", "low", "close"]] = [93.2, 93.25, 92.9, 93.]
    assert not gate("ENTER", False, "SHORT", frame, exit_info, "TEST", live_price=93.0)


def test_mid_trend_exit_rejects_ma_alignment_without_pivot_or_breakout():
    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "ma3": 100.0, "ma15": 101.0,
         "kc_upper": 105.0, "kc_lower": 95.0, "kc_middle": 101.0, "timestamp": 60_000},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "ma3": 100.0, "ma15": 100.0,
         "kc_upper": 105.0, "kc_lower": 95.0, "kc_middle": 100.0, "timestamp": 120_000},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "ma3": 100.0, "ma15": 99.0,
         "kc_upper": 105.0, "kc_lower": 95.0, "kc_middle": 99.0, "timestamp": 180_000},
        {"open": 100.0, "high": 101.0, "low": 98.0, "close": 99.0, "ma3": 98.0, "ma15": 98.5,
         "kc_upper": 105.0, "kc_lower": 95.0, "kc_middle": 99.0, "timestamp": 240_000},
    ]
    frame = pd.DataFrame(rows)
    exit_info = {"side": "LONG", "exit_bar_id": 60_000, "require_new_closed_break": True}
    gate = TradingEngine._channel_peak_exit_reentry_blocked
    assert gate("ENTER", False, "SHORT", frame, exit_info, "TEST", live_price=98.0)


def test_confirmed_opposite_outer_breakout_requires_two_closed_bodies():
    rows = [
        {"open": 99.0, "high": 100.0, "low": 98.0, "close": 99.5, "kc_upper": 105.0, "kc_lower": 95.0},
        {"open": 100.0, "high": 106.0, "low": 99.0, "close": 105.5, "kc_upper": 105.0, "kc_lower": 95.0},
        {"open": 105.5, "high": 107.0, "low": 105.0, "close": 106.5, "kc_upper": 105.0, "kc_lower": 95.0},
        {"open": 106.5, "high": 108.0, "low": 106.0, "close": 107.0, "kc_upper": 105.0, "kc_lower": 95.0},
    ]
    frame = pd.DataFrame(rows)
    assert opposite_outer_breakout_side(frame, 107.0, "SHORT") == "LONG"
    frame.loc[2, "close"] = frame.loc[2, "open"]
    assert opposite_outer_breakout_side(frame, 107.0, "SHORT") is None