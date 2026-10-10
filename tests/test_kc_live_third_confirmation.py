import pytest

from core.services.entry_contract import ENTRY_CODES, evaluate_entry_contract
from test_breakout_only_entry import breakout_frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_closed_two_bar_breakout_is_the_only_live_confirmation(side):
    frame = breakout_frame(side)
    live = frame.iloc[-1]
    rail = "kc_upper" if side == "LONG" else "kc_lower"
    sign = 1 if side == "LONG" else -1
    # Keep the quote outside the current rail and give the short fixture a
    # same-bar pullback rejection required by the anti-bottom guard.
    if side == "SHORT":
        quote = float(live[rail]) - 0.15
        frame.loc[frame.index[-1], ["high", "low"]] = [
            float(live[rail]) + 0.1, quote - 0.01,
        ]
    else:
        quote = float(live[rail]) + 0.05
    decision = evaluate_entry_contract(frame, quote)

    assert decision is not None
    assert decision["type"] == f"KC_2BAR_CONFIRM_{side}"
    assert decision["entry_phase"] == "KC_2BAR_CLOSED_CONFIRM"
    assert decision["breakout_bar_id"] == frame.iloc[-3]["timestamp"]
    assert decision["pair_confirmation_bar_id"] == frame.iloc[-2]["timestamp"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_old_third_candle_authority_is_not_whitelisted(side):
    frame = breakout_frame(side)

    assert f"KC_2BAR_CONFIRM_{side}" in ENTRY_CODES
    assert f"KC_3BAR_CONFIRM_{side}" not in ENTRY_CODES
    assert evaluate_entry_contract(
        frame, code=f"KC_3BAR_CONFIRM_{side}"
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_color_cannot_replace_an_invalid_closed_confirmation(side):
    frame = breakout_frame(side)
    confirmation = frame.index[-2]
    frame.loc[confirmation, "open"] = (
        frame.loc[confirmation, "close"] + (0.1 if side == "LONG" else -0.1)
    )

    assert evaluate_entry_contract(frame, symbol="TEST") is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_quote_must_still_follow_the_confirmed_breakout(side):
    frame = breakout_frame(side)
    frame.loc[frame.index[-1], "close"] = frame.loc[frame.index[-1], "open"]
    rail = "kc_upper" if side == "LONG" else "kc_lower"
    quote = float(frame.iloc[-1][rail]) - 0.1 if side == "LONG" else float(frame.iloc[-1][rail]) + 0.1

    assert evaluate_entry_contract(frame, quote, symbol="TEST") is None
