import pytest
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from core.services.entry_contract import ENTRY_CODES, evaluate_entry_contract
from test_breakout_only_entry import breakout_frame, live_outer_frame














@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_no_outer_rail_break_means_no_entry(side):
    frame = breakout_frame(side)
    live = frame.index[-1]
    lower = float(frame.loc[live, "kc_lower"])
    upper = float(frame.loc[live, "kc_upper"])
    quote = (lower + upper) / 2

    assert evaluate_entry_contract(frame, quote, symbol="TEST/USDT") is None






@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_body_breakout_rejects_small_body_and_late_chase(side):
    frame = live_outer_frame(side)
    live = frame.index[-1]
    lower = float(frame.loc[live, "kc_lower"])
    upper = float(frame.loc[live, "kc_upper"])
    rail = upper if side == "LONG" else lower
    code = f"KC_LIVE_BODY_BREAKOUT_{side}"

    frame.loc[live, "open"] = rail
    small_body_quote = rail + 0.1 if side == "LONG" else rail - 0.1
    assert evaluate_entry_contract(frame, small_body_quote, code, symbol="TEST/USDT") is None

    late_quote = rail + 3.1 if side == "LONG" else rail - 3.1
    assert evaluate_entry_contract(frame, late_quote, code, symbol="TEST/USDT") is None








@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_body_breakout_still_rejects_open_outside_opposite_rail(side):
    frame = live_outer_frame(side)
    live = frame.index[-1]
    lower = float(frame.loc[live, "kc_lower"])
    upper = float(frame.loc[live, "kc_upper"])
    opened = lower - 0.1 if side == "LONG" else upper + 0.1
    quote = upper + 0.7 if side == "LONG" else lower - 0.7
    frame.loc[live, ["open", "close", "high", "low"]] = [
        opened, quote, max(opened, quote), min(opened, quote),
    ]

    assert evaluate_entry_contract(
        frame, quote, f"KC_LIVE_BODY_BREAKOUT_{side}", symbol="TEST/USDT"
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_directional_k1_must_begin_inside_the_kc_channel(side):
    frame = breakout_frame(side)
    index = frame.index[-3]
    edge = "kc_upper" if side == "LONG" else "kc_lower"
    frame.loc[index, "open"] = (
        frame.loc[index, edge] + (0.1 if side == "LONG" else -0.1)
    )

    assert evaluate_entry_contract(frame, symbol="TEST/USDT") is None
