import pytest

from core.services.entry_contract import ENTRY_CODES, evaluate_entry_contract
from test_breakout_only_entry import breakout_frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_valid_two_closed_bar_breakout_is_directional(side):
    frame = breakout_frame(side)
    decision = evaluate_entry_contract(frame, symbol="TEST/USDT")

    assert decision is not None
    assert decision["side"] == side
    assert decision["type"] == f"KC_2BAR_CONFIRM_{side}"
    assert decision["breakout_bar_id"] < decision["pair_confirmation_bar_id"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_single_body_breakout_cannot_bypass_closed_pair(side):
    frame = breakout_frame(side)
    frame.loc[1, "close"] = frame.loc[1, "open"]
    frame.loc[1, "high"] = max(frame.loc[1, "high"], frame.loc[1, "open"])
    frame.loc[1, "low"] = min(frame.loc[1, "low"], frame.loc[1, "open"])

    assert evaluate_entry_contract(frame, symbol="TEST/USDT") is None
    assert f"KC_LIVE_BODY_BREAKOUT_{side}" not in ENTRY_CODES


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_directional_k1_must_begin_inside_the_kc_channel(side):
    frame = breakout_frame(side)
    index = frame.index[-3]
    edge = "kc_upper" if side == "LONG" else "kc_lower"
    frame.loc[index, "open"] = (
        frame.loc[index, edge] + (0.1 if side == "LONG" else -0.1)
    )

    assert evaluate_entry_contract(frame, symbol="TEST/USDT") is None
