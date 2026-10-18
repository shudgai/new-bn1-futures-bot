import pandas as pd
import pytest

from core.gates.ma5_ma15_cross_entry_gate import (
    ma_cross_gate_exempt_for_entry,
    validate_ma5_ma15_cross_entry,
)
from core.services.entry_firewall import market_structure_gate_problem


def _long_cross_frame(cross_age):
    count = max(3, cross_age + 2)
    cross_index = count - 1 - cross_age
    rows = []
    for index in range(count):
        ma15 = 100.0 + index * 0.01
        if index < cross_index:
            ma5 = ma15 - 0.2
        elif index == cross_index:
            ma5 = ma15 + 0.05
        else:
            ma5 = 100.2 + (index - cross_index - 1) * 0.1
        rows.append({
            "timestamp": (index + 1) * 60_000,
            "is_closed": True,
            "open": 99.5,
            "high": 100.1,
            "low": 99.4,
            "close": 100.0,
            "ma5": ma5,
            "ma15": ma15,
            "kc_trend": "UP",
            "kc_upper": 110.0,
            "kc_middle": 99.0 + index * 0.1,
            "kc_lower": 95.0,
            "atr": 4.0,
        })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def _short_cross_frame():
    rows = []
    for index in range(4):
        ma15 = 100.0 - index * 0.01
        ma5 = ma15 + 0.2 if index < 3 else ma15 - 0.05
        rows.append({
            "timestamp": (index + 1) * 60_000,
            "is_closed": True,
            "open": 100.0,
            "high": 100.2,
            "low": 99.7,
            "close": 99.8,
            "ma5": ma5,
            "ma15": ma15,
            "kc_trend": "DOWN",
            "kc_upper": 105.0,
            "kc_middle": 100.0 - index * 0.1,
            "kc_lower": 90.0,
            "atr": 4.0,
        })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def _aligned_market_structure_frame(ma_gap=0.2, *, falling_ma5=False):
    rows = []
    for index in range(15):
        ma15 = 100.0 + index * 0.1
        ma5 = ma15 + ma_gap
        if falling_ma5:
            ma5 = 102.0 - index * 0.01
        rows.append({
            "timestamp": (index + 1) * 60_000,
            "is_closed": True,
            "open": 100.0 + index * 0.1,
            "high": 101.0 + index * 0.1,
            "low": 99.0 + index * 0.1,
            "close": 100.5 + index * 0.1,
            "ma5": ma5,
            "ma15": ma15,
            "kc_upper": 105.0 + index * 0.1,
            "kc_middle": 100.0 + index * 0.1,
            "kc_lower": 95.0 + index * 0.1,
            "atr": 1.0,
        })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


@pytest.mark.parametrize("cross_age", [1, 2])
def test_long_cross_is_allowed_only_in_first_two_post_cross_bars(cross_age):
    allowed, reason, details = validate_ma5_ma15_cross_entry(
        _long_cross_frame(cross_age), "LONG",
    )

    assert allowed, reason
    assert details["cross_age_bars"] == cross_age


@pytest.mark.parametrize("cross_age", [0, 3])
def test_long_cross_outside_post_cross_window_is_blocked(cross_age):
    allowed, reason, details = validate_ma5_ma15_cross_entry(
        _long_cross_frame(cross_age), "LONG",
    )

    assert not allowed
    assert reason == "BLOCKED_MA5_MA15_CROSS_TOO_OLD"
    assert details["cross_age_bars"] == cross_age


def test_long_cross_rejects_quote_above_kc_upper():
    allowed, reason, _ = validate_ma5_ma15_cross_entry(
        _long_cross_frame(1), "LONG", price=110.01,
    )

    assert not allowed
    assert reason == "BLOCKED_LONG_ABOVE_KC_UPPER"


def test_long_cross_allows_exact_half_atr_ma15_bias_and_blocks_more():
    frame = _long_cross_frame(1)
    ma15 = float(frame.iloc[-1]["ma15"])

    allowed, reason, details = validate_ma5_ma15_cross_entry(
        frame, "LONG", price=ma15 + 2.0,
    )
    assert allowed, reason
    assert details["ma15_bias_atr"] == pytest.approx(0.5)

    allowed, reason, _ = validate_ma5_ma15_cross_entry(
        frame, "LONG", price=ma15 + 2.01,
    )
    assert not allowed
    assert reason == "BLOCKED_LONG_MA15_BIAS"


def test_long_cross_requires_strictly_rising_ma5():
    frame = _long_cross_frame(1)
    frame.loc[frame.index[-1], "ma5"] = frame.iloc[-2]["ma5"]

    allowed, reason, _ = validate_ma5_ma15_cross_entry(frame, "LONG")

    assert not allowed
    assert reason == "BLOCKED_LONG_MA5_NOT_RISING"


def test_short_cross_rejects_quote_below_kc_lower():
    allowed, reason, _ = validate_ma5_ma15_cross_entry(
        _short_cross_frame(), "SHORT", price=89.99,
    )

    assert not allowed
    assert reason == "BLOCKED_SHORT_BELOW_KC_LOWER"


def test_short_cross_rejects_ma15_bias_over_half_atr():
    allowed, reason, details = validate_ma5_ma15_cross_entry(
        _short_cross_frame(), "SHORT", price=97.8,
    )

    assert not allowed
    assert reason == "BLOCKED_SHORT_MA15_BIAS"
    assert details["ma15_bias_atr"] > 0.5


def test_short_cross_allows_exact_half_atr_ma15_bias():
    frame = _short_cross_frame()
    allowed, reason, details = validate_ma5_ma15_cross_entry(
        frame, "SHORT", price=97.97,
    )

    assert allowed, reason
    assert details["ma15_bias_atr"] == pytest.approx(0.5)


@pytest.mark.parametrize(
    "frame,expected_reason",
    [
        (_aligned_market_structure_frame(0.14), "VETO_MA_TOO_CLOSE"),
        (
            _aligned_market_structure_frame(falling_ma5=True),
            "VETO_LINE_PARALLEL_OR_FLAT_MA5",
        ),
    ],
)
def test_confirmed_outer_entry_still_requires_ma_gap_and_direction(
    frame, expected_reason,
):
    reason = market_structure_gate_problem(
        frame,
        "LONG",
        is_confirmed_outer_entry=True,
        enforce_ma_direction=True,
    )

    assert reason is not None
    assert reason.startswith(expected_reason)


@pytest.mark.parametrize(
    ("entry_type", "entry_phase"),
    [
        ("KC_2BAR_CONFIRM_LONG", "KC_2BAR_CLOSED_CONFIRM"),
        ("AUTHORIZED_BY_TREND_CONTINUATION_LONG", "POST_CLOSE_CONTINUATION_ENTRY"),
        ("KC_LIVE_BODY_BREAKOUT_LONG", "KC_LIVE_OUTER_BREAKOUT"),
        ("BULLISH_INSTANT_BREAKOUT", "REALTIME_RAIL_BREACH"),
        ("KC_TREND_LONG", "KC_TREND_ENTRY"),
    ],
)
def test_breakout_and_continuation_entries_bypass_ma_cross_gate(
    entry_type, entry_phase,
):
    assert ma_cross_gate_exempt_for_entry(entry_type, entry_phase)


def test_ma_cross_entry_is_not_misclassified_as_breakout_exemption():
    assert not ma_cross_gate_exempt_for_entry(
        "KC_GOLDEN_CROSS_FAST_LONG", "MA_GOLDEN_CROSS_FAST_LANE",
    )
