import pandas as pd
import pytest

from core.services.kc_pending_entry import evaluate_kc_pending_entry
from test_breakout_only_entry import breakout_frame


def closed_bars(side):
    frame = breakout_frame(side)
    return frame[frame["is_closed"]].reset_index(drop=True), frame.iloc[-1]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_two_closed_same_color_breakout_bars_authorize_confirmation(side):
    closed, live = closed_bars(side)
    quote = float(live["close"])

    result = evaluate_kc_pending_entry(closed, quote, live=live)

    assert result["action"] == "ENTER"
    assert result["side"] == side
    assert result["type"] == f"KC_2BAR_CONFIRM_{side}"
    assert result["breakout_bar_id"] == closed.iloc[-2]["timestamp"]
    assert result["pair_confirmation_bar_id"] == closed.iloc[-1]["timestamp"]


@pytest.mark.parametrize(
    ("side", "mutation"),
    [
        ("LONG", "gap_open"),
        ("LONG", "second_opposite"),
        ("LONG", "second_touch"),
        ("SHORT", "gap_open"),
        ("SHORT", "second_opposite"),
        ("SHORT", "second_touch"),
    ],
)
def test_invalid_two_bar_breakout_never_authorizes_entry(side, mutation):
    closed, live = closed_bars(side)
    breakout_index, confirmation_index = closed.index[-2], closed.index[-1]
    edge = "kc_upper" if side == "LONG" else "kc_lower"
    sign = 1 if side == "LONG" else -1

    if mutation == "gap_open":
        closed.loc[breakout_index, "open"] = (
            closed.loc[breakout_index, edge] + sign * 0.1
        )
    elif mutation == "second_opposite":
        closed.loc[confirmation_index, "open"] = (
            closed.loc[confirmation_index, "close"] - sign * 0.1
        )
    elif mutation == "second_touch":
        closed.loc[confirmation_index, "close"] = closed.loc[confirmation_index, edge]

    result = evaluate_kc_pending_entry(closed, float(live["close"]), live=live)

    assert result["action"] == "WAIT"
    assert result["reason"] == "WAIT_NEW_KC_BREAKOUT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_quote_must_remain_strictly_beyond_outer_rail(side):
    closed, live = closed_bars(side)
    rail = float(closed.iloc[-1]["kc_upper" if side == "LONG" else "kc_lower"])

    result = evaluate_kc_pending_entry(closed, rail, live=live)

    assert result["action"] == "WAIT"
    assert result["reason"] == "KC_PENDING_CANCELLED_INSIDE_RAIL"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ma5_alignment_and_direction_remain_required(side):
    closed, live = closed_bars(side)
    closed.loc[closed.index[-1], "ma5"] = closed.iloc[-2]["ma5"]

    result = evaluate_kc_pending_entry(closed, float(live["close"]), live=live)

    assert result["action"] == "WAIT"
    assert result["reason"] == "WAIT_NEW_KC_BREAKOUT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_alternate_entry_codes_are_rejected(side):
    closed, live = closed_bars(side)

    result = evaluate_kc_pending_entry(
        closed, float(live["close"]), code=f"KC_OUTER_PIVOT_{side}", live=live
    )

    assert result["action"] == "WAIT"
    assert result["reason"] == "WAIT_NEW_KC_BREAKOUT"
