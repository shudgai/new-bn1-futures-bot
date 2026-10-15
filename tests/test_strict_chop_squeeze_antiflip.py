import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest

from core.gates.pipeline import (
    EntryGatePipeline,
    STRATEGY_TYPE_BREAKOUT,
    ma5_ma15_cross_chop_problem,
    strict_kc_entry_problem,
)
from core.services.entry_contract import (
    evaluate_entry_contract,
    opposite_side_close_cooldown_problem,
)
from core.services.entry_firewall import validate_account_entry


def entry_frame(
    spreads,
    *,
    upper=101.0,
    middle=100.0,
    lower=99.0,
    quote=100.0,
):
    rows = []
    base_timestamp = 1_800_000_000_000
    for index, spread in enumerate(spreads):
        rows.append({
            "timestamp": base_timestamp + index * 60_000,
            "is_closed": index < len(spreads) - 1,
            "open": 100.0,
            "high": max(101.0, quote),
            "low": min(99.0, quote),
            "close": quote if index == len(spreads) - 1 else 100.0,
            "atr": 0.5,
            "ma5": 100.0 + spread,
            "ma15": 100.0,
            "kc_upper": upper,
            "kc_middle": middle,
            "kc_lower": lower,
        })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame






@pytest.mark.parametrize("side", [None, "LONG", "SHORT"])
def test_kc_width_below_point_six_percent_blocks_every_side(side):
    frame = entry_frame([0.2] * 10, upper=100.29, middle=100.0, lower=99.71)

    assert strict_kc_entry_problem(frame, 100.0, side) == "BLOCKED_KC_SQUEEZE"


@pytest.mark.parametrize(
    ("side", "quote", "expected"),
    [
        ("LONG", 100.51, "INSUFFICIENT_UPPER_SPACE"),
        ("LONG", 100.50, None),
        ("SHORT", 99.49, "INSUFFICIENT_LOWER_SPACE"),
        ("SHORT", 99.50, None),
    ],
)
def test_directional_room_uses_strict_twenty_five_percent_boundary(
    side, quote, expected,
):
    frame = entry_frame([0.2] * 10, quote=quote)
    frame.loc[frame.index[-1], "atr"] = 1.0

    assert strict_kc_entry_problem(frame, quote, side) == expected




def test_same_side_entry_is_not_misclassified_as_opposite_flip():
    frame = entry_frame([0.2] * 10)
    close_timestamp = float(frame.iloc[-1]["timestamp"])
    account = SimpleNamespace(
        last_closed_at={"CAP/USDT": close_timestamp / 1000.0},
        last_closed_side={"CAP/USDT": "LONG"},
    )

    assert opposite_side_close_cooldown_problem(
        account, "CAP/USDT", frame, "LONG",
    ) is None


def test_pipeline_rejects_squeeze_before_route_authorization():
    frame = entry_frame(
        [0.2] * 10, upper=100.29, middle=100.0, lower=99.71,
    )
    diagnostics = {}

    decision = EntryGatePipeline().authorize(
        {"type": "AUTHORIZED_BY_MIDLINE_BREAK_LONG", "side": "LONG"},
        frame,
        100.0,
        symbol="CAP/USDT",
        requested_side="LONG",
        diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"] == "BLOCKED_KC_SQUEEZE"








