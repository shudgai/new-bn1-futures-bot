"""The retired three-bar helper cannot override the active two-bar KC authority."""
import pytest

from core.services.kc_pending_entry import evaluate_kc_pending_entry
from core.services.three_bar_rail_gate import three_bar_rail_gate_problem
from test_kc_pending_entry import closed_bars


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_legacy_three_bar_gate_is_not_an_entry_veto(side):
    closed, live = closed_bars(side)

    assert three_bar_rail_gate_problem(
        closed, float(live["close"]), side,
    ) is None


@pytest.mark.parametrize("symbol", ["CAP/USDT", "LOBSTER/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_active_two_bar_outer_rail_authority_is_shared(symbol, side):
    closed, live = closed_bars(side)

    decision = evaluate_kc_pending_entry(
        closed, float(live["close"]), symbol=symbol, live=live,
    )

    assert decision["action"] == "ENTER"
    assert decision["type"] == f"KC_2BAR_CONFIRM_{side}"
