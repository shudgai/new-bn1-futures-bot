import pytest

from core.services.entry_contract import ENTRY_CODES, evaluate_entry_contract
from test_breakout_only_entry import breakout_frame






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
