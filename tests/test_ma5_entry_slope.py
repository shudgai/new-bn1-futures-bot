import pandas as pd
import pytest

from core.services.ma5_entry_slope import (
    MIN_MA5_SLOPE_ATR,
    live_ma5_slope_atr,
    live_ma5_slope_problem,
)


def frame_for(side, delta_atr, *, closed=True):
    sign = 1.0 if side == "LONG" else -1.0
    rows = [
        dict(ma5=100.0, close=100.0, atr=2.0, is_closed=True),
        dict(ma5=100.0 + sign * delta_atr * 2.0,
             close=100.0, atr=2.0, is_closed=closed),
    ]
    return pd.DataFrame(rows)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_aligned_slope_at_or_above_minimum_is_accepted(side):
    frame = frame_for(side, MIN_MA5_SLOPE_ATR)
    quote = 100.0

    assert live_ma5_slope_atr(frame, quote) == pytest.approx(
        MIN_MA5_SLOPE_ATR if side == "LONG" else -MIN_MA5_SLOPE_ATR
    )
    assert live_ma5_slope_problem(frame, quote, side) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_aligned_but_shallow_slope_is_rejected(side):
    frame = frame_for(side, MIN_MA5_SLOPE_ATR * 0.98)

    assert live_ma5_slope_problem(frame, 100.0, side) == (
        "BLOCKED_BY_MA5_SLOPE_INSUFFICIENT"
    )


@pytest.mark.parametrize(
    "side,wrong_side,expected",
    [
        ("LONG", "SHORT", "BLOCKED_BY_MA5_SLOPE_DOWN"),
        ("SHORT", "LONG", "BLOCKED_BY_MA5_SLOPE_UP"),
    ],
)
def test_slope_in_opposite_direction_is_rejected(side, wrong_side, expected):
    frame = frame_for(wrong_side, MIN_MA5_SLOPE_ATR)

    assert live_ma5_slope_problem(frame, 100.0, side) == expected


def preflight_frame(side, slope_atr):
    sign = 1.0 if side == "LONG" else -1.0
    return pd.DataFrame([
        dict(timestamp=1_000, open=100.0, close=100.0, high=100.5, low=99.5,
             atr=1.0, ma5=100.0, ma15=100.0,
             kc_upper=110.0, kc_middle=100.0, kc_lower=90.0, is_closed=True),
        dict(timestamp=2_000, open=100.0, close=100.0, high=100.5, low=99.5,
             atr=1.0, ma5=100.0 + sign * slope_atr, ma15=100.0,
             kc_upper=110.0, kc_middle=100.0, kc_lower=90.0, is_closed=True),
    ])


def test_strong_breakout_cannot_bypass_minimum_ma5_slope(monkeypatch):
    from types import SimpleNamespace
    from core.services.entry_contract import pre_flight_safety_check
    from core.gates.pipeline import pipeline
    from core.intelligence.spatial_brain import SpatialBrain

    monkeypatch.setattr(pipeline, "has_strong_bullish_breakout", lambda *_: True)
    monkeypatch.setattr(
        SpatialBrain, "analyze",
        lambda *_: SimpleNamespace(state="EXPLOSIVE_EXPANSION"),
    )
    frame = preflight_frame("LONG", MIN_MA5_SLOPE_ATR * 0.5)

    passed, reason = pre_flight_safety_check(
        frame, 100.0, "LONG", decision_type="CLOSED_BODY_BREAKOUT_LONG",
    )

    assert not passed
    assert reason == "BLOCKED_BY_MA5_SLOPE_INSUFFICIENT"


def test_waterfall_flip_can_enter_with_aligned_shallow_turn(monkeypatch):
    from types import SimpleNamespace
    from core.services.entry_contract import pre_flight_safety_check
    from core.intelligence.spatial_brain import SpatialBrain

    monkeypatch.setattr(
        SpatialBrain, "analyze",
        lambda *_: SimpleNamespace(state="EXPLOSIVE_EXPANSION"),
    )
    frame = preflight_frame("SHORT", MIN_MA5_SLOPE_ATR * 0.5)

    passed, reason = pre_flight_safety_check(
        frame, 100.0, "SHORT", decision_type="TOP_WATERFALL_FLIP",
    )

    assert passed
    assert reason is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pure_trend_route_also_blocks_a_shallow_ma5_slope(monkeypatch, side):
    from core.services.strategies import pure_trend_v2
    from core.services.strategies.pure_trend_v2 import evaluate_v2_frame

    rows = [
        dict(timestamp=1_000, open=100., close=100.2, high=100.4, low=99.8,
             atr=1., ma3=100.2, ma5=100., ma15=100., kc_lower=99.,
             kc_middle=100., kc_upper=101., is_closed=True),
        dict(timestamp=61_000, open=100., close=102., high=102.2, low=99.9,
             atr=1., ma3=100.5, ma5=100., ma15=100., kc_lower=99.,
             kc_middle=100., kc_upper=101., is_closed=True),
        dict(timestamp=121_000, open=102., close=103., high=103.2, low=101.9,
             atr=1., ma3=101., ma5=100., ma15=100., kc_lower=100.,
             kc_middle=101., kc_upper=102., is_closed=True),
        dict(timestamp=181_000, open=103., close=103.5, high=103.6, low=102.9,
             atr=1., ma3=102., ma5=100.025, ma15=100., kc_lower=100.,
             kc_middle=101., kc_upper=102., is_closed=False),
    ]
    frame = pd.DataFrame(rows)
    if side == "SHORT":
        original = frame.copy()
        for key in ("open", "close", "ma3", "ma5", "ma15", "kc_lower", "kc_middle", "kc_upper"):
            source = {"kc_lower": "kc_upper", "kc_upper": "kc_lower"}.get(key, key)
            frame[key] = 200.0 - original[source]
        frame["high"] = 200.0 - original["low"]
        frame["low"] = 200.0 - original["high"]
    frame.attrs["timeframe_ms"] = 60_000
    closed = frame.iloc[:-1]
    monkeypatch.setattr(
        "core.services.strategies.unified_entry_strategy.confirmed",
        lambda _frame: closed,
    )
    monkeypatch.setattr(
        pure_trend_v2.PureTrendStrategyV2,
        "evaluate_second_bar_outside_entry",
        lambda _self, _symbol, _row, _previous, _closed: {
            "side": side, "type": f"SECOND_BAR_OUTSIDE_{side}",
            "initial_sl": 99.0 if side == "LONG" else 101.0,
        },
    )
    monkeypatch.setattr(
        pure_trend_v2.PureTrendStrategyV2,
        "outside_continuation_side", lambda _self, _closed: None,
    )

    quote = 103.5 if side == "LONG" else 96.5
    diagnostics = {}
    result = evaluate_v2_frame(frame, quote, diagnostics=diagnostics)

    assert result is None
    assert diagnostics["reason"] == "BLOCKED_BY_MA5_SLOPE_INSUFFICIENT"
