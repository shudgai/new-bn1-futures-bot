"""Regression tests for the mandatory three-bar KC rail entry gate."""
import pandas as pd
import pytest

from core.services.three_bar_rail_gate import three_bar_rail_gate_problem
from core.services import entry_contract


def bars(side="LONG"):
    rows = [
        dict(timestamp=60000, open=100., close=102. if side == "LONG" else 98.,
             high=102.2 if side == "LONG" else 100.2,
             low=99.8 if side == "LONG" else 97.8,
             kc_upper=101., kc_lower=99., atr=1., ma3=100., ma5=100., is_closed=True),
        dict(timestamp=120000, open=102. if side == "LONG" else 98.,
             close=103. if side == "LONG" else 97.,
             high=103.2 if side == "LONG" else 98.2,
             low=101.8 if side == "LONG" else 96.8,
             kc_upper=101., kc_lower=99., atr=1., ma3=101., ma5=101., is_closed=True),
        dict(timestamp=180000, open=103. if side == "LONG" else 97.,
             close=103.5 if side == "LONG" else 96.5,
             high=103.6 if side == "LONG" else 97.1,
             low=102.9 if side == "LONG" else 96.4,
             kc_upper=101., kc_lower=99., atr=1., ma3=102., ma5=102., is_closed=False),
    ]
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60000
    return frame


@pytest.mark.parametrize("symbol", ["CAP/USDT", "LOBSTER/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_three_bar_directional_pattern_passes_for_both_symbols(symbol, side):
    assert symbol in ("CAP/USDT", "LOBSTER/USDT")
    frame = bars(side)
    assert three_bar_rail_gate_problem(frame, frame.iloc[-1].close, side) is None


@pytest.mark.parametrize("symbol", ["CAP/USDT", "LOBSTER/USDT"])
def test_long_without_bar1_upper_rail_close_is_hard_rejected(symbol):
    frame = bars("LONG")
    frame.loc[0, "close"] = 100.9
    assert three_bar_rail_gate_problem(frame, 103.5, "LONG") == \
        "BLOCKED_THREE_BAR_LONG_NOT_BROKEN_UPPER_RAIL"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_bar2_must_confirm_same_color(side):
    frame = bars(side)
    frame.loc[1, ["open", "close"]] = (
        (103.1, 102.9) if side == "LONG" else (96.9, 97.1)
    )
    assert "BAR2_NOT" in three_bar_rail_gate_problem(
        frame, frame.iloc[-1].close, side,
    )


@pytest.mark.parametrize("body", [0.4, 0.41, 0.6])
def test_strong_green_reversal_invalidates_short_setup(body):
    frame = bars("SHORT")
    opening = 97.
    frame.loc[2, ["open", "close", "high", "low", "ma3", "ma5"]] = (
        opening, opening + body, opening + body, opening, 110., 111.
    )
    assert three_bar_rail_gate_problem(frame, opening + body, "SHORT") == \
        "BLOCKED_THREE_BAR_SHORT_STRONG_REVERSAL"


def test_short_green_close_above_ma_invalidates_even_if_body_is_weak():
    frame = bars("SHORT")
    frame.loc[2, ["open", "close", "high", "low", "ma3", "ma5"]] = (
        97., 97.2, 97.2, 97., 97.1, 97.15,
    )
    assert three_bar_rail_gate_problem(frame, 97.2, "SHORT") == \
        "BLOCKED_THREE_BAR_SHORT_STRONG_REVERSAL"


def test_short_green_engulfing_bar_invalidates_setup():
    frame = bars("SHORT")
    frame.loc[2, ["open", "close", "high", "low"]] = (96.8, 97.2, 97.3, 96.7)
    assert three_bar_rail_gate_problem(frame, 97.2, "SHORT") == \
        "BLOCKED_THREE_BAR_SHORT_STRONG_REVERSAL"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_weak_opposite_trigger_bar_is_allowed(side):
    frame = bars(side)
    if side == "LONG":
        frame.loc[2, ["open", "close", "high", "low", "ma3", "ma5"]] = (103., 102.8, 103.1, 102.7, 102.7, 102.6)
        quote = 102.8
    else:
        frame.loc[2, ["open", "close", "high", "low", "ma3", "ma5"]] = (97.2, 97.4, 97.5, 97.1, 97.6, 97.7)
        quote = 97.4
    assert three_bar_rail_gate_problem(frame, quote, side) is None


def test_live_trigger_uses_quote_and_prior_closed_atr():
    frame = bars("SHORT").iloc[:2].copy()
    live = bars("SHORT").iloc[-1].copy()
    live["timestamp"] = 180000
    live["is_closed"] = False
    live["open"] = 97.
    live["close"] = 97.
    frame = pd.concat([frame, live.to_frame().T], ignore_index=True)
    frame.attrs["timeframe_ms"] = 60000
    assert three_bar_rail_gate_problem(frame, 97.41, "SHORT") == \
        "BLOCKED_THREE_BAR_SHORT_STRONG_REVERSAL"


def test_bar3_must_be_live_while_bar1_and_bar2_are_closed():
    frame = bars("LONG")
    frame.loc[2, "is_closed"] = True
    assert three_bar_rail_gate_problem(frame, 103.5, "LONG") == \
        "BLOCKED_THREE_BAR_GATE_BAR3_NOT_LIVE"


@pytest.mark.parametrize("symbol", ["CAP/USDT", "LOBSTER/USDT"])
@pytest.mark.parametrize("violation,side,expected", [
    ("unbroken_upper", "LONG", "BLOCKED_THREE_BAR_LONG_NOT_BROKEN_UPPER_RAIL"),
    ("strong_reversal", "SHORT", "BLOCKED_THREE_BAR_SHORT_STRONG_REVERSAL"),
])
def test_entry_contract_rejects_pending_candidate_at_shared_authority(
    monkeypatch, symbol, violation, side, expected,
):
    frame = bars(side)
    quote = float(frame.iloc[-1].close)
    if violation == "unbroken_upper":
        frame.loc[0, "close"] = 100.9
    else:
        frame.loc[2, ["open", "close", "high", "low", "ma3", "ma5"]] = (
            97., 97.5, 97.5, 97., 110., 111.,
        )
        quote = 97.5

    monkeypatch.setattr(entry_contract, "evaluate_kc_pending_entry", lambda *a, **k: {
        "action": "ENTER", "side": side, "type": "KC_PENDING_TEST",
        "pending_signal_id": f"{symbol}:{violation}",
    })
    monkeypatch.setattr(entry_contract, "entry_direction_problem", lambda *a: None)
    monkeypatch.setattr(entry_contract, "entry_trend_alignment_ready", lambda *a: True)
    monkeypatch.setattr(entry_contract, "entry_consolidation_problem", lambda *a, **k: None)
    monkeypatch.setattr(entry_contract, "anti_bottom_short_problem", lambda *a, **k: None)
    diagnostics = {}
    decision = entry_contract.evaluate_entry_contract(
        frame, quote, symbol=symbol, diagnostics=diagnostics,
    )
    assert decision is None
    assert diagnostics["reason"] == expected


@pytest.mark.parametrize("symbol", ["CAP/USDT", "LOBSTER/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_valid_three_bar_pending_candidate_reaches_shared_authority(
    monkeypatch, symbol, side,
):
    frame = bars(side)
    quote = float(frame.iloc[-1].close)
    pending = {
        "action": "ENTER", "side": side, "type": "KC_PENDING_TEST",
        "pending_signal_id": f"{symbol}:valid:{side}",
    }
    monkeypatch.setattr(entry_contract, "evaluate_kc_pending_entry", lambda *a, **k: pending)
    monkeypatch.setattr(entry_contract, "entry_direction_problem", lambda *a: None)
    monkeypatch.setattr(entry_contract, "entry_trend_alignment_ready", lambda *a: True)
    monkeypatch.setattr(entry_contract, "entry_consolidation_problem", lambda *a, **k: None)
    monkeypatch.setattr(entry_contract, "anti_bottom_short_problem", lambda *a, **k: None)
    decision = entry_contract.evaluate_entry_contract(frame, quote, symbol=symbol)
    assert decision is pending
