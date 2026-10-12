import pandas as pd
import pytest

from core.services.ma5_entry_slope import (
    MIN_MA5_SLOPE_ATR,
    live_ma5_entry_direction_problem,
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

    monkeypatch.setattr(pipeline, "has_strong_bullish_breakout", lambda *_: False)
    monkeypatch.setattr(
        SpatialBrain, "analyze",
        lambda *_: SimpleNamespace(state="EXPLOSIVE_EXPANSION"),
    )
    monkeypatch.setattr(
        "core.services.entry_contract.entry_market_structure_problem",
        lambda *_args, **_kwargs: None,
    )
    frame = preflight_frame("LONG", MIN_MA5_SLOPE_ATR * 0.5)
    frame.loc[frame.index[-1], "close"] = 100.1

    passed, reason = pre_flight_safety_check(
        frame, 100.1, "LONG", decision_type="CLOSED_BODY_BREAKOUT_LONG",
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
    monkeypatch.setattr(
        "core.services.entry_contract.entry_market_structure_problem",
        lambda *_args, **_kwargs: None,
    )
    frame = preflight_frame("SHORT", MIN_MA5_SLOPE_ATR * 0.5)

    passed, reason = pre_flight_safety_check(
        frame, 99.8, "SHORT", decision_type="TOP_WATERFALL_FLIP",
    )

    assert passed, reason
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


@pytest.mark.parametrize(
    "previous_ma5,current_ma5,quote,expected",
    [
        (100.0, 100.0, 100.0, "BLOCKED_BY_MA5_WEAK_SLOPE"),
        (100.1, 100.0, 100.0, "BLOCKED_BY_MA5_WEAK_SLOPE"),
        (99.9, 100.0, 99.9, "BLOCKED_BY_MA5_WEAK_SLOPE"),
        (99.9, 100.0, 100.0, None),
    ],
)
def test_live_ma5_direction_gate_requires_rising_line_and_price_above(
    previous_ma5, current_ma5, quote, expected,
):
    frame = pd.DataFrame([
        dict(ma5=previous_ma5, close=100.0, is_closed=True),
        dict(ma5=current_ma5, close=100.0, is_closed=True),
    ])

    assert live_ma5_entry_direction_problem(frame, quote, "LONG") == expected


@pytest.mark.parametrize(
    "previous_ma5,current_ma5,quote,expected",
    [
        (100.0, 100.0, 100.0, "BLOCKED_BY_MA5_STRONG_SLOPE"),
        (99.9, 100.0, 100.0, "BLOCKED_BY_MA5_STRONG_SLOPE"),
        (100.1, 100.0, 100.1, "BLOCKED_BY_MA5_STRONG_SLOPE"),
        (100.1, 100.0, 100.0, None),
    ],
)
def test_live_ma5_direction_gate_requires_falling_line_and_price_below(
    previous_ma5, current_ma5, quote, expected,
):
    frame = pd.DataFrame([
        dict(ma5=previous_ma5, close=100.0, is_closed=True),
        dict(ma5=current_ma5, close=100.0, is_closed=True),
    ])

    assert live_ma5_entry_direction_problem(frame, quote, "SHORT") == expected


@pytest.mark.parametrize(
    "side,previous_ma5,current_ma5,quote,expected",
    [
        ("LONG", 100.1, 100.0, 100.0, "BLOCKED_BY_MA5_WEAK_SLOPE"),
        ("SHORT", 99.9, 100.0, 100.0, "BLOCKED_BY_MA5_STRONG_SLOPE"),
    ],
)
def test_ma5_direction_gate_is_applied_by_pipeline_authorizer(
    monkeypatch, side, previous_ma5, current_ma5, quote, expected,
):
    import importlib
    from core.gates.pipeline import EntryGatePipeline

    pipeline_module = importlib.import_module("core.gates.pipeline")
    frame = pd.DataFrame([
        dict(timestamp=(index + 1) * 60_000, open=100.0, close=100.0,
             high=101.0, low=99.0, atr=1.0, ma5=100.2,
             ma15=99.8, kc_upper=102.0, kc_middle=100.0, kc_lower=98.0,
             is_closed=True)
        for index in range(6)
    ])
    frame.loc[frame.index[-2], "ma5"] = previous_ma5
    frame.loc[frame.index[-1], "ma5"] = current_ma5
    frame.attrs["timeframe_ms"] = 60_000
    monkeypatch.setattr(
        pipeline_module, "ma5_ma15_cross_chop_problem", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        pipeline_module, "strict_kc_entry_problem", lambda *_a, **_k: None,
    )
    gate = EntryGatePipeline()
    monkeypatch.setattr(gate, "market_regime_problem", lambda *_a, **_k: None)
    monkeypatch.setattr(
        pipeline_module, "entry_market_structure_problem",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(gate, "chop_lockout_problem", lambda *_a, **_k: None)
    if side == "LONG":
        monkeypatch.setattr(
            gate, "detect_midline_break_long",
            lambda *_a, **_k: {
                "type": "AUTHORIZED_BY_MIDLINE_BREAK_LONG",
                "side": side,
                "pending_signal_id": "test-long",
            },
        )
    else:
        monkeypatch.setattr(
            gate, "detect_midline_break_short",
            lambda *_a, **_k: {
                "type": "AUTHORIZED_BY_MIDLINE_BREAK_SHORT",
                "side": side,
                "pending_signal_id": "test-short",
            },
        )

    diagnostics = {}
    decision_type = (
        "AUTHORIZED_BY_MIDLINE_BREAK_LONG"
        if side == "LONG" else "AUTHORIZED_BY_MIDLINE_BREAK_SHORT"
    )
    decision = gate.authorize(
        {
            "type": decision_type,
            "side": side,
            "pending_signal_id": f"test-{side.lower()}",
        },
        frame,
        quote,
        requested_side=side,
        diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"] == expected


@pytest.mark.parametrize(
    "side,previous_ma5,current_ma5,quote,expected",
    [
        ("LONG", 100.1, 100.0, 100.0, "BLOCKED_BY_MA5_WEAK_SLOPE"),
        ("SHORT", 99.9, 100.0, 100.0, "BLOCKED_BY_MA5_STRONG_SLOPE"),
    ],
)
def test_entry_preflight_enforces_ma5_direction_for_all_routes(
    monkeypatch, side, previous_ma5, current_ma5, quote, expected,
):
    from core.services import entry_contract

    frame = pd.DataFrame([
        dict(timestamp=60_000, open=100.0, close=100.0, high=101.0,
             low=99.0, atr=1.0, ma5=previous_ma5, ma15=99.8,
             kc_upper=102.0, kc_middle=100.0, kc_lower=98.0,
             is_closed=True),
        dict(timestamp=120_000, open=100.0, close=100.0, high=101.0,
             low=99.0, atr=1.0, ma5=current_ma5, ma15=99.9,
             kc_upper=102.0, kc_middle=100.0, kc_lower=98.0,
             is_closed=True),
    ])
    monkeypatch.setattr(
        entry_contract, "entry_market_structure_problem", lambda *_a, **_k: None,
    )

    passed, reason = entry_contract.pre_flight_safety_check(
        frame, quote, side, decision_type="AUTHORIZED_REVERSAL_ROUTE",
    )

    assert passed is False
    assert reason == expected
