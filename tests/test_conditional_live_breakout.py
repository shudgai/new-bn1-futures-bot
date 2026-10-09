import pytest

from core.services.entry_contract import ENTRY_CODES, evaluate_entry_contract
from test_breakout_only_entry import breakout_frame, inner_channel_frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_valid_two_closed_bar_breakout_is_directional(side):
    frame = breakout_frame(side)
    decision = evaluate_entry_contract(frame, symbol="TEST/USDT")

    assert decision is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_continuation_signal_is_rejected_before_entry_evaluation(side):
    frame = breakout_frame(side)
    diagnostics = {}
    assert evaluate_entry_contract(
        frame, code=f"KC_OUTSIDE_{side}", symbol="TEST/USDT",
        diagnostics=diagnostics,
    ) is None
    assert diagnostics["reason"] == "BLOCKED_OBSOLETE_ENTRY_SIGNAL"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_body_breakout_enters_on_first_qualifying_bar(side):
    frame = inner_channel_frame(side)
    live = frame.index[-1]
    quote = float(frame.loc[live, "close"])

    decision = evaluate_entry_contract(
        frame, quote, symbol="TEST/USDT"
    )

    assert f"KC_LIVE_BODY_BREAKOUT_{side}" in ENTRY_CODES
    assert decision is not None
    assert decision["side"] == side
    assert decision["type"] == f"KC_LIVE_BODY_BREAKOUT_{side}"
    assert decision["entry_phase"] == "KC_INNER_CHANNEL_PRESSURE"
    assert decision["confirmation_bar_id"] == frame.iloc[-1]["timestamp"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_body_breakout_rejects_small_body_and_late_chase(side):
    frame = inner_channel_frame(side)
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
def test_live_body_breakout_requires_open_inside_channel(side):
    frame = inner_channel_frame(side)
    live = frame.index[-1]
    lower = float(frame.loc[live, "kc_lower"])
    upper = float(frame.loc[live, "kc_upper"])
    rail = upper if side == "LONG" else lower
    frame.loc[live, "open"] = rail + 0.1 if side == "LONG" else rail - 0.1
    quote = rail + 0.7 if side == "LONG" else rail - 0.7

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
