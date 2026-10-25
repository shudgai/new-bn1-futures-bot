import pandas as pd
import pytest

import core.gates.holding_protection_gate as holding_gate
from core.gates.holding_protection_gate import HoldingProtectionExitGate
from core.gates.meme_coin_gate import MemeCoinGate
from core.gates.unified_pipeline_gates import ExitGate
from core.services.exits.confirmed_pressure_exit import evaluate_confirmed_pressure_exit
from core.services.exits.authorized_position_exit import automated_position_exit_allowed


def make_frame(side, current, *, live=None):
    rows = []
    push_close = 100.1 if side == "LONG" else 99.9
    for index in range(3):
        rows.append({
            "timestamp": 1_800_000_000_000 + index * 60_000,
            "open": 100.0, "high": 100.2, "low": 99.8, "close": push_close,
            "ma3": 99.9, "ma15": 99.5, "atr": 1.0,
            "kc_upper": 102.0, "kc_lower": 98.0, "kc_middle": 100.0,
            "volume": 10.0, "is_closed": True,
        })
    rows[-1].update(current)
    if live:
        live_row = dict(rows[-1])
        live_row.update(live)
        live_row["timestamp"] += 60_000
        live_row["is_closed"] = False
        rows.append(live_row)
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def position(side="LONG", *, entry_timestamp=1_800_000_000.0):
    return {
        "symbol": "龍蝦/USDT", "side": side,
        "entry_mode": "CHANNEL_SWING", "entry_price": 100.0,
        "open_timestamp": entry_timestamp,
    }


@pytest.mark.parametrize("side,current,expected", [
    (
        "LONG",
        {"open": 100.0, "high": 100.1, "low": 99.5, "close": 99.8,
         "ma3": 99.7, "ma15": 99.0},
        None,
    ),
    (
        "LONG",
        {"open": 100.0, "high": 100.0, "low": 99.2, "close": 99.4,
         "ma3": 99.7, "ma15": 99.6},
        "EXIT_BY_CONFIRMED_SELLING_PRESSURE",
    ),
    (
        "SHORT",
        {"open": 100.0, "high": 100.8, "low": 100.0, "close": 100.6,
         "ma3": 100.3, "ma15": 100.4},
        "EXIT_BY_CONFIRMED_BUYING_PRESSURE",
    ),
])
def test_structural_break_requires_closed_bar_and_both_key_averages(
    side, current, expected,
):
    frame = make_frame(side, current)
    result = evaluate_confirmed_pressure_exit(position(side), frame, side)

    assert result["reason"] == expected
    assert result["details"]["bar_closed"] is True


@pytest.mark.parametrize("side,current,expected", [
    (
        "LONG",
        {"open": 100.0, "high": 103.0, "low": 99.75, "close": 99.8,
         "ma3": 99.0, "ma15": 98.0, "kc_upper": 102.0},
        "EXIT_BY_CONFIRMED_SELLING_PRESSURE",
    ),
    (
        "SHORT",
        {"open": 100.0, "high": 100.25, "low": 97.0, "close": 100.2,
         "ma3": 101.0, "ma15": 102.0, "kc_lower": 98.0},
        "EXIT_BY_CONFIRMED_BUYING_PRESSURE",
    ),
])
def test_outer_rail_blowoff_requires_directional_close_and_long_wick(
    side, current, expected,
):
    result = evaluate_confirmed_pressure_exit(
        position(side), make_frame(side, current), side,
    )

    assert result["reason"] == expected
    assert result["details"]["exhaustion_blowoff"] is True


@pytest.mark.parametrize("side,current,expected", [
    (
        "LONG",
        {"open": 100.0, "high": 100.0, "low": 99.4, "close": 99.4,
         "ma3": 99.6, "ma15": 99.0, "volume": 15.0},
        "EXIT_BY_CONFIRMED_SELLING_PRESSURE",
    ),
    (
        "SHORT",
        {"open": 100.0, "high": 100.6, "low": 100.0, "close": 100.6,
         "ma3": 100.4, "ma15": 101.0, "volume": 15.0},
        "EXIT_BY_CONFIRMED_BUYING_PRESSURE",
    ),
])
def test_half_atr_adverse_body_breaking_ma3_is_pressure(side, current, expected):
    result = evaluate_confirmed_pressure_exit(
        position(side), make_frame(side, current), side,
    )

    assert result["reason"] == expected
    assert result["details"]["heavy_selling"] is True


def test_heavy_body_and_ma3_break_are_sufficient_with_low_volume_metadata():
    current = {
        "open": 100.0, "high": 100.0, "low": 99.4, "close": 99.4,
        "ma3": 99.6, "ma15": 99.0, "volume": 14.99,
    }

    result = evaluate_confirmed_pressure_exit(
        position(), make_frame("LONG", current), "LONG",
    )

    assert result["reason"] == "EXIT_BY_CONFIRMED_SELLING_PRESSURE"
    assert result["details"]["volume_confirmed"] is False


@pytest.mark.parametrize("side,current", [
    (
        "LONG",
        {"open": 100.0, "high": 100.1, "low": 99.0, "close": 99.4,
         "ma3": 99.8, "ma15": 99.5},
    ),
    (
        "SHORT",
        {"open": 100.0, "high": 101.0, "low": 99.9, "close": 100.6,
         "ma3": 100.2, "ma15": 100.5},
    ),
])
def test_strong_supporting_wick_vetoes_pressure_exit(side, current):
    result = evaluate_confirmed_pressure_exit(
        position(side), make_frame(side, current), side,
    )

    assert result["reason"] is None
    assert result["details"]["classification"] == "HEALTHY_PULLBACK"


def test_live_reversal_is_ignored_until_the_bar_closes():
    mild_closed = {
        "open": 100.0, "high": 100.1, "low": 99.9, "close": 99.9,
        "ma3": 99.8, "ma15": 99.5,
    }
    heavy_live = {
        "open": 100.0, "high": 100.0, "low": 99.2, "close": 99.4,
        "ma3": 99.7, "ma15": 99.6, "volume": 15.0,
    }
    frame = make_frame("LONG", mild_closed, live=heavy_live)

    result = evaluate_confirmed_pressure_exit(position(), frame, "LONG")

    assert result["reason"] is None
    assert result["details"]["bar_id"] == frame.iloc[-2]["timestamp"]


def test_entry_bar_cannot_confirm_pressure_exit():
    current = {
        "open": 100.0, "high": 100.0, "low": 99.2, "close": 99.4,
        "ma3": 99.7, "ma15": 99.6, "volume": 15.0,
    }
    frame = make_frame("LONG", current)
    same_bar_position = position(entry_timestamp=1_800_000_120.0)

    assert evaluate_confirmed_pressure_exit(
        same_bar_position, frame, "LONG",
    )["reason"] is None


def test_confirmed_pressure_is_only_a_candidate_not_exit_authority():
    current = {
        "open": 100.0, "high": 100.0, "low": 99.2, "close": 99.4,
        "ma3": 99.7, "ma15": 99.6, "volume": 15.0,
    }
    frame = make_frame("LONG", current)
    pos = position()

    allowed, reason = MemeCoinGate.evaluate_meme_exit(
        frame, quote=98.0, side="LONG", symbol="龍蝦/USDT", position=pos,
    )
    assert allowed
    assert reason == "EXIT_BY_CONFIRMED_SELLING_PRESSURE"
    assert not automated_position_exit_allowed(
        "龍蝦/USDT", pos, {}, reason, 98.0, frame=frame,
    )


def test_holding_gate_rejects_pressure_without_true_pivot(monkeypatch):
    current = {
        "open": 100.0, "high": 100.0, "low": 99.2, "close": 99.4,
        "ma3": 99.7, "ma15": 99.6,
    }
    frame = make_frame("LONG", current)
    monkeypatch.setattr(
        holding_gate, "has_confirmed_true_pivot",
        lambda *_args, **_kwargs: (False, None, {}),
    )

    allowed, reason, details = HoldingProtectionExitGate.validate_exit(
        position(), frame, 99.4,
        "EXIT_BY_CONFIRMED_SELLING_PRESSURE", channel_swing=True,
    )

    assert not allowed
    assert reason == "VETO_HOLDING_UNTIL_TRUE_PIVOT"
    assert "classification" not in details


def test_exit_gate_holds_on_confirmed_pressure_without_lock_or_true_pivot():
    current = {
        "open": 100.0, "high": 100.0, "low": 99.2, "close": 99.4,
        "ma3": 99.7, "ma15": 99.6,
    }
    frame = make_frame("LONG", current)
    pos = position()
    market = {
        "raw_df": frame, "current_price": 99.4, "atr": 1.0,
        "kc_upper": 102.0, "kc_lower": 98.0, "kc_middle": 100.0,
        "kc_middle_slope": 0.1, "macro_trend": "BULLISH",
        "ma3": 99.7, "prev_ma3": 99.6, "ma15": 99.6,
    }

    should_exit, reason, meta = ExitGate.evaluate(pos, market)

    assert not should_exit
    assert reason == "HOLD_LOCKED"
    assert meta["exit_policy"] == "HOLD_UNTIL_HARD_STOP_TRUE_PIVOT_OR_TWO_OF_FOUR_FACTORS"
    assert meta["suppressed_exit_reason"] is None
