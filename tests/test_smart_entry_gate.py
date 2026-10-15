from types import SimpleNamespace

import pandas as pd
import pytest

from core.gates.smart_entry_gate import (
    SMART_ENTRY_CODES,
    smart_entry_candidate,
    smart_entry_gate_problem,
)


def _frame(side):
    rows = []
    start = 1_700_000_000_000
    for index in range(40):
        if side == "LONG":
            middle = 99.0 + index * 0.01
            row = {
                "open": 100.2, "high": 100.9, "low": 99.0, "close": 100.5,
                "ma5": middle + 0.15, "kc_middle": middle,
                "kc_upper": middle + 3.0, "kc_lower": middle - 3.0,
            }
        else:
            middle = 101.0 - index * 0.01
            row = {
                "open": 99.8, "high": 101.0, "low": 99.0, "close": 99.5,
                "ma5": middle - 0.15, "kc_middle": middle,
                "kc_upper": middle + 3.0, "kc_lower": middle - 3.0,
            }
        row.update(timestamp=start + index * 60_000, volume=1_000.0)
        rows.append(row)

    breakout = rows[28]
    if side == "LONG":
        breakout.update(open=100.8, high=101.3, low=100.7, close=101.2, volume=1_500.0)
        middle = rows[39]["kc_middle"]
        rows[39].update(
            open=middle + 0.10, high=middle + 0.40, low=middle + 0.02,
            close=middle + 0.25,
        )
    else:
        breakout.update(open=99.2, high=99.3, low=98.7, close=98.8, volume=1_500.0)
        middle = rows[39]["kc_middle"]
        rows[39].update(
            open=middle - 0.10, high=middle - 0.02, low=middle - 0.40,
            close=middle - 0.25,
        )

    frame = pd.DataFrame(rows)
    frame["is_closed"] = True
    frame.attrs["timeframe_ms"] = 60_000
    return frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_volume_breakout_then_confirmed_pullback_passes_smart_gate(side):
    candidate, reason = smart_entry_candidate(
        _frame(side), side, symbol="LOBSTER/USDT",
    )

    assert reason is None
    assert candidate["side"] == side
    assert candidate["type"] == SMART_ENTRY_CODES[side]
    assert candidate["entry_phase"] == "SMART_ENTRY_PULLBACK"
    assert candidate["breakout_bar_id"] < candidate["confirmation_bar_id"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_two_midline_crosses_block_both_directions(side):
    frame = _frame(side)
    for index, sign in zip((32, 33, 34), (-1, 1, -1)):
        middle = frame.loc[index, "kc_middle"]
        close = middle + sign * 0.2
        frame.loc[index, ["open", "high", "low", "close"]] = [
            close - 0.05 if sign > 0 else close + 0.05,
            close + 0.1,
            close - 0.1,
            close,
        ]

    problem, _ = smart_entry_gate_problem(frame, side)

    assert problem.startswith("BLOCKED_SMART_ENTRY_CHOP_CROSSES")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_box_interior_without_volume_backed_breakout_is_blocked(side):
    frame = _frame(side)
    frame.loc[28, "volume"] = 1_499.0

    problem, _ = smart_entry_gate_problem(frame, side)

    assert problem == "BLOCKED_SMART_ENTRY_INSIDE_RANGE"


@pytest.mark.parametrize(
    ("side", "rail_column", "expected"),
    [
        ("LONG", "kc_upper", "BLOCKED_SMART_ENTRY_LONG_OUTER_CHASE"),
        ("SHORT", "kc_lower", "BLOCKED_SMART_ENTRY_SHORT_OUTER_CHASE"),
    ],
)
def test_outer_rail_is_never_a_chase_entry(side, rail_column, expected):
    frame = _frame(side)
    if side == "LONG":
        close = frame.loc[39, rail_column]
        frame.loc[39, ["open", "high", "low", "close"]] = [
            close - 0.15, close + 0.1, frame.loc[39, "kc_middle"] + 0.02, close,
        ]
    else:
        close = frame.loc[39, rail_column]
        frame.loc[39, ["open", "high", "low", "close"]] = [
            close + 0.15, frame.loc[39, "kc_middle"] - 0.02, close - 0.1, close,
        ]

    problem, _ = smart_entry_gate_problem(frame, side)

    assert problem == expected


def test_cooldown_requires_five_fully_closed_bars_after_exit():
    frame = _frame("LONG")
    account = SimpleNamespace(last_closed_at={
        "LOBSTER/USDT": (float(frame.loc[35, "timestamp"]) + 60_000) / 1000.0,
    })

    problem, _ = smart_entry_gate_problem(
        frame, "LONG", account=account, symbol="LOBSTER/USDT",
    )
    assert problem.startswith("BLOCKED_SMART_ENTRY_COOLDOWN")

    account.last_closed_at["LOBSTER/USDT"] = (
        float(frame.loc[34, "timestamp"]) + 60_000
    ) / 1000.0
    problem, _ = smart_entry_gate_problem(
        frame, "LONG", account=account, symbol="LOBSTER/USDT",
    )
    assert problem is None


def test_gate_fails_closed_when_history_is_too_short():
    problem, _ = smart_entry_gate_problem(_frame("LONG").tail(20), "LONG")

    assert problem == "BLOCKED_SMART_ENTRY_INSUFFICIENT_OR_INVALID_DATA"
