import asyncio

import pandas as pd
import pytest

from core.gates import kc_overall_structure_gate
from core.gates.kc_overall_structure_gate import (
    LONG_SLOPE_LOOKBACK,
    check_kc_overall_structure,
    kc_midline_slope_evidence,
)
from core.services.entry_contract import automatic_entry_kc_structure_problem


def _frame(direction="UP", *, live_row=False):
    rows = []
    for index in range(15):
        sign = -1 if direction == "DOWN" else 1
        upper = 110.0 + sign * index
        middle = 100.0 + sign * index * 0.6
        lower = 90.0 + sign * index * 0.4
        if direction == "BOX":
            upper, middle, lower = 110.0, 100.0, 90.0
        elif direction == "MISALIGNED":
            lower = 90.0 - index * 0.4
        rows.append({
            "timestamp": 60_000 * (index + 1),
            "kc_upper": upper,
            "kc_middle": middle,
            "kc_lower": lower,
            "atr": 1.0,
            "is_closed": True,
        })
    if live_row:
        rows.append({
            "timestamp": 60_000 * (len(rows) + 1),
            "kc_upper": 150.0,
            "kc_middle": 140.0,
            "kc_lower": 130.0,
            "atr": 1.0,
            "is_closed": False,
        })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("UP", "UP_ALIGNED"),
        ("DOWN", "DOWN_ALIGNED"),
        ("BOX", "CHOP_BOX"),
        ("MISALIGNED", "CHOP_BOX"),
    ],
)
def test_check_kc_overall_structure_classifies_direction_and_box(direction, expected):
    assert check_kc_overall_structure(_frame(direction)) == expected


def test_check_kc_overall_structure_ignores_live_candle():
    assert check_kc_overall_structure(_frame("DOWN", live_row=True)) == "DOWN_ALIGNED"


def test_midline_slope_evidence_uses_configured_long_lookback():
    assert LONG_SLOPE_LOOKBACK == 15
    evidence = kc_midline_slope_evidence(_frame("UP"))

    assert evidence == {"short_slope": pytest.approx(1.2), "long_slope": pytest.approx(8.4)}


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame(),
        pd.DataFrame([{"kc_upper": 1.0} for _ in range(8)]),
    ],
)
def test_check_kc_overall_structure_fails_closed_on_insufficient_data(frame):
    assert check_kc_overall_structure(frame) == "CHOP_BOX"


def test_automatic_entry_gate_logs_and_blocks_box_structure(monkeypatch, caplog):
    monkeypatch.setattr(
        kc_overall_structure_gate,
        "check_kc_overall_structure",
        lambda _frame: "CHOP_BOX",
    )

    problem = automatic_entry_kc_structure_problem(
        "SHORT", _frame(), {}, "AUTOMATIC_ENTRY",
    )

    assert problem == "BLOCKED_KC_OVERALL_STRUCTURE:CHOP_BOX"
    assert "[攔截開單] KC 整體處於水平箱體或三軌未同步，放棄進場" in caplog.text


def test_automatic_entry_gate_does_not_block_manual_orders(monkeypatch):
    monkeypatch.setattr(
        kc_overall_structure_gate,
        "check_kc_overall_structure",
        lambda _frame: "CHOP_BOX",
    )

    assert automatic_entry_kc_structure_problem(
        "LONG", _frame(), {"manual_entry": True}, "MANUAL",
    ) is None


def test_wait_entry_authority_does_not_inherit_kc_strategy_gate(monkeypatch):
    monkeypatch.setattr(
        kc_overall_structure_gate,
        "check_kc_overall_structure",
        lambda _frame: "CHOP_BOX",
    )

    assert automatic_entry_kc_structure_problem(
        "LONG", _frame(), {"entry_authority": "WAIT"}, "AUTOMATIC_ENTRY",
    ) is None


@pytest.mark.parametrize(("side", "direction"), [("LONG", "UP"), ("SHORT", "DOWN")])
def test_automatic_entry_gate_accepts_aligned_bands_and_both_slopes(side, direction):
    assert automatic_entry_kc_structure_problem(
        side, _frame(direction), {}, "AUTOMATIC_ENTRY",
    ) is None


@pytest.mark.parametrize(
    ("side", "structure", "slopes"),
    [
        ("SHORT", "DOWN_ALIGNED", {"short_slope": -1.0, "long_slope": 2.0}),
        ("LONG", "UP_ALIGNED", {"short_slope": 1.0, "long_slope": -2.0}),
    ],
)
def test_automatic_entry_requires_both_midline_slopes_to_match(
    monkeypatch, side, structure, slopes,
):
    monkeypatch.setattr(
        kc_overall_structure_gate,
        "check_kc_overall_structure",
        lambda _frame: structure,
    )
    monkeypatch.setattr(
        kc_overall_structure_gate,
        "kc_midline_slope_evidence",
        lambda _frame: slopes,
    )

    problem = automatic_entry_kc_structure_problem(
        side, _frame("DOWN" if side == "SHORT" else "UP"), {},
        "AUTOMATIC_ENTRY",
    )

    assert problem == "BLOCKED_KC_OVERALL_STRUCTURE:MIDLINE_SLOPE_MISMATCH"


def test_execution_manager_rejects_box_before_submitting_order():
    from core.services.execution_manager import ExecutionManager

    submitted = []

    class Account:
        positions = {}
        position_meta = {}

        async def open_position(self, **kwargs):
            submitted.append(kwargs)
            return True

    opened = asyncio.run(ExecutionManager.open_position(
        Account(),
        "LOBSTER/USDT",
        "LONG",
        100.0,
        "AUTOMATIC_ENTRY",
        meta={"raw_df": _frame("BOX")},
    ))

    assert not opened
    assert submitted == []
    assert ExecutionManager._last_open_error["LOBSTER/USDT"] == (
        "BLOCKED_KC_OVERALL_STRUCTURE:CHOP_BOX"
    )
