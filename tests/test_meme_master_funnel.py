"""Owner-approved closed impulse, shared filters, and strict holding policy."""

import math
import time
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd

import pytest

from core.gates.master_gate_funnel import (
    MarketSnapshot, PositionSnapshot, MasterGateFunnel, evaluate_meme_entry_frame,
)
from core.services.entry_methods import MEME_GATE_ENTRY, evaluate_entry_method, revalidate_entry_method
from core.services.exits.authorized_position_exit import (
    UltimateExitGatekeeper, automated_position_exit_allowed,
)
from core.services.math_execution_gate import validate_exit_at_execution, validate_exit_math
from test_early_midline_entry import early_frame
from test_entry_methods import method_context

SYMBOLS = ["龙虾/USDT", "CAP/USDT", "1000PEPE/USDT"]


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("body", [0.999999, 1.0, 1.000001])
@pytest.mark.parametrize("prior_alignment", [-.000001, 0, .000001])
def test_bottom_v_long_requires_alignment_before_the_entry_bar(symbol, body, prior_alignment):
    m = market(symbol=symbol)._replace(
        open_price=101.7 - body, low_price=101.7 - body - .01,
        ma5_prev=101 + prior_alignment,
    )
    expected = body >= 1 and prior_alignment > 0
    assert MasterGateFunnel.is_bottom_v_long(m) is expected


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("fault,reason", [
    ("macro", "BLOCKED_LONG_MACRO_TREND_CONFLICT"),
    ("kc", "BLOCKED_LONG_KC_DIRECTION"),
    ("quote", "BLOCKED_LONG_PRICE_MIDDLE_MA15"),
    ("bias", "BLOCKED_LONG_MA15_EXTENSION_1_ATR"),
    ("chop", "VETO_MA5_MA15_CHOP_8"),
    ("unclosed", "VETO_INVALID_MARKET_DATA"),
    ("cooldown", "BLOCKED_BY_POST_EXIT_COOLDOWN"),
])
def test_bottom_v_long_never_bypasses_global_guards(symbol, fault, reason):
    m = pivot_market("LONG", symbol, open_price=100.7, low_price=100.69,
                     ma5_prev=100.99)
    assert MasterGateFunnel.is_bottom_pivot_long(m)
    if fault == "macro":
        m = m._replace(macro_trend="BEARISH")
    elif fault == "kc":
        m = m._replace(kc_middle_prev=m.kc_middle + .01)
    elif fault == "quote":
        m = m._replace(quote=m.ma15_curr)
    elif fault == "bias":
        m = m._replace(quote=m.ma15_curr + 1.5 * m.atr + .01)
    elif fault == "chop":
        m = m._replace(ma_cross_count_8=3)
    elif fault == "unclosed":
        m = m._replace(is_closed=False)
    elif fault == "cooldown":
        m = m._replace(ma5_prev=m.ma15_prev - 0.01)
    result = MasterGateFunnel.evaluate_open_long(m, fault == "cooldown")
    if fault == "kc":
        assert not result.can_execute
        assert result.reason == "BLOCKED_LONG_KC_MIDDLE_TOO_FLAT"
        return
    if fault == "macro":
        assert result.can_execute
        assert result.details["priority_channel"] == "TRIGGER_BOTTOM_PIVOT_CROSS"
        return
    if fault == "cooldown":
        # A fresh golden cross above KC middle/MA15 is a new wave start: zero-second cooldown exemption.
        assert result.can_execute
        assert result.details["priority_channel"] == "TRIGGER_BOTTOM_PIVOT_CROSS"
        stale = m._replace(ma5_prev=m.ma15_prev + 0.01)
        blocked = MasterGateFunnel.evaluate_open_long(stale, True)
        assert not blocked.can_execute and blocked.reason == reason
        return
    assert not result.can_execute and result.reason == reason


def market(side="LONG", symbol="CAP/USDT", **updates):
    data = dict(
        symbol=symbol, quote=101.7, open_price=100, close_price=101.7,
        high_price=101.8, low_price=99.9, ma3_curr=0, ma3_prev=0,
        ma5_curr=101.2, ma5_prev=101.1, ma15_curr=101.0, ma15_prev=101.0,
        kc_upper=103, kc_lower=99, kc_middle=101, kc_middle_prev=100.9,
        atr=2, macro_trend="BULLISH", ma_cross_count_8=0, is_closed=True,
    )
    data.update(updates)
    if side == "SHORT":
        for key in ("quote", "open_price", "close_price", "ma5_curr", "ma5_prev",
                    "ma15_curr", "ma15_prev", "kc_middle", "kc_middle_prev"):
            data[key] = 200 - data[key]
        data["high_price"], data["low_price"] = 200 - data["low_price"], 200 - data["high_price"]
        data["kc_upper"], data["kc_lower"] = 200 - data["kc_lower"], 200 - data["kc_upper"]
        data["macro_trend"] = "BEARISH"
    return MarketSnapshot(**data)


def pivot_market(side, symbol="1000PEPE/USDT", **updates):
    """Closed reversal candle in its authorized outer KC location."""
    if side == "SHORT":
        data = dict(
            quote=98.3, open_price=99.1, close_price=98.3,
            high_price=99.11, low_price=98.29,
            ma5_curr=98.8, ma5_prev=99.1,
            ma15_curr=99.0, ma15_prev=99.0,
            kc_upper=98.6, kc_lower=97.5, kc_middle=98.05,
            kc_middle_prev=98.15, macro_trend="BEARISH",
        )
    else:
        data = dict(
            quote=101.7, open_price=100.9, close_price=101.7,
            high_price=101.71, low_price=100.89,
            ma5_curr=101.2, ma5_prev=100.9,
            ma15_curr=101.0, ma15_prev=101.0,
            kc_upper=102.4, kc_lower=101.4, kc_middle=101.9,
            kc_middle_prev=101.79, macro_trend="BULLISH",
        )
    data.update(updates)
    return market(side, symbol)._replace(**data)


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", [
    None, "macro", "opposite_kc", "ma_flat", "ma_equal", "chop",
    "quote_middle", "close_middle", "quote_bias", "close_bias", "unclosed",
    "nan_atr", "bad_ohlc", "cooldown",
])
def test_expansion_never_bypasses_shared_direction_and_safety(symbol, side, fault):
    m = market(side, symbol)
    sign = 1 if side == "LONG" else -1
    m = m._replace(ma5_prev=m.ma15_prev + sign * .01)
    if fault == "macro":
        m = m._replace(macro_trend="BEARISH" if side == "LONG" else "BULLISH")
    elif fault == "opposite_kc":
        m = m._replace(kc_middle_prev=m.kc_middle + sign * 0.01)
    elif fault == "ma_flat":
        m = m._replace(ma5_prev=m.ma5_curr)
    elif fault == "ma_equal":
        m = m._replace(ma15_curr=m.ma5_curr)
    elif fault == "chop":
        m = m._replace(ma_cross_count_8=3)
    elif fault == "quote_middle":
        m = m._replace(quote=m.kc_middle)
    elif fault == "close_middle":
        m = m._replace(close_price=m.kc_middle)
    elif fault == "quote_bias":
        m = m._replace(quote=m.ma15_curr + sign * 3.000001)
    elif fault == "close_bias":
        close = m.ma15_curr + sign * 2.000001
        m = m._replace(close_price=close, high_price=max(close, m.high_price), low_price=min(close, m.low_price))
    elif fault == "unclosed":
        m = m._replace(is_closed=False)
    elif fault == "nan_atr":
        m = m._replace(atr=math.nan)
    elif fault == "bad_ohlc":
        m = m._replace(high_price=m.low_price)
    decision = (
        MasterGateFunnel.evaluate_open_long(m, fault == "cooldown") if side == "LONG"
        else MasterGateFunnel.evaluate_open_short(m, fault == "cooldown")
    )
    # The fixture is a fresh MA5/MA15 cross beyond KC middle (priority channel): it is exempt
    # from the lagging KC slope and the cooldown. A close-only bias that is a fresh big-body
    # rail break is measured from the bar open (early breakout/breakdown channel).
    assert decision.can_execute is (fault is None), decision
    if fault == "cooldown":
        stale = m._replace(ma5_prev=m.ma15_prev + sign * .01)
        stale_decision = (
            MasterGateFunnel.evaluate_open_long(stale, True) if side == "LONG"
            else MasterGateFunnel.evaluate_open_short(stale, True)
        )
        assert stale_decision.reason == "BLOCKED_BY_POST_EXIT_COOLDOWN"
    if fault == "close_bias":
        assert decision.details["priority_channel"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("offset,allowed", [(-0.000001, False), (0, True), (0.000001, True)])
def test_flat_expansion_body_threshold_is_inclusive(side, offset, allowed):
    m = market(side)
    sign = 1 if side == "LONG" else -1
    m = m._replace(ma5_prev=m.ma15_prev + sign * .1)
    body = (0.8 if side == "LONG" else 0.7) * m.atr + offset
    opened = m.close_price - sign * body
    m = m._replace(open_price=opened, low_price=min(opened, m.close_price) - .01,
                   high_price=max(opened, m.close_price) + .01)
    if side == "SHORT":
        m = m._replace(ma5_prev=98.9)
    assert MasterGateFunnel.is_expansion(m, side) is allowed


@pytest.mark.parametrize("offset,allowed", [(-0.000001, True), (0, True), (0.000001, False)])
def test_long_squeeze_upper_band_allowance_is_1_5_atr(offset, allowed):
    m = market(
        quote=104 + offset, close_price=103.2, open_price=101,
        high_price=103.5, low_price=100.9, kc_middle=100, kc_middle_prev=99.9,
        kc_upper=101, kc_lower=99, ma15_curr=102.5, ma15_prev=102.5,
        ma5_curr=102.6, ma5_prev=102.55,
    )
    assert MasterGateFunnel.evaluate_open_long(m).can_execute is allowed


@pytest.mark.parametrize("offset,allowed", [(-0.000001, True), (0, True), (0.000001, False)])
def test_short_lower_band_allowance_is_point_8_atr(offset, allowed):
    m = market("SHORT")._replace(kc_lower=99.9, kc_middle=100, kc_middle_prev=100.1, quote=98.3 - offset)
    assert MasterGateFunnel.evaluate_open_short(m).can_execute is allowed


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("body", [0.799999, 0.8, 0.800001])
@pytest.mark.parametrize("alignment", [-0.000001, 0.0, 0.000001])
def test_early_long_point_four_atr_allows_equality_but_not_wrong_alignment(symbol, body, alignment):
    m = market(
        symbol=symbol, open_price=101.7 - body, low_price=100.2,
        ma5_curr=100.8 + alignment, ma5_prev=100.75,
        ma15_curr=100.8, ma15_prev=100.7,
        kc_middle=100.3, kc_middle_prev=100.0,
    )
    result = MasterGateFunnel.evaluate_open_long(m)
    expected = body >= .8 and alignment > 0
    assert result.can_execute is expected
    if expected:
        assert result.reason == "AUTHORIZED_LONG:TRIGGER_EARLY_0748_BREAKOUT"


@pytest.mark.parametrize("fault", [
    "macro", "opposite_kc", "flat_ma5", "unclosed", "quote_middle",
    "quote_ma15", "chop", "bias", "band_extension", "cooldown",
])
def test_early_long_exemption_never_disables_other_gates(fault):
    m = market(open_price=100.9, low_price=100.89, ma5_curr=101, ma5_prev=100.9)
    if fault == "macro":
        m = m._replace(macro_trend="BEARISH")
    elif fault == "opposite_kc":
        m = m._replace(kc_middle_prev=101.01)
    elif fault == "flat_ma5":
        m = m._replace(ma5_prev=m.ma5_curr)
    elif fault == "unclosed":
        m = m._replace(is_closed=False)
    elif fault == "quote_middle":
        m = m._replace(quote=m.kc_middle)
    elif fault == "quote_ma15":
        m = m._replace(quote=m.ma15_curr)
    elif fault == "chop":
        m = m._replace(ma_cross_count_8=3)
    elif fault == "bias":
        m = m._replace(quote=m.ma15_curr + 1.5 * m.atr + .000001)
    elif fault == "band_extension":
        m = m._replace(kc_upper=101.1, quote=102.700001)
    assert not MasterGateFunnel.evaluate_open_long(m, fault == "cooldown").can_execute


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("body", [0.799999, 0.8, 0.800001])
@pytest.mark.parametrize("prior_alignment", [-0.000001, 0.0, 0.000001])
def test_top_pivot_short_requires_point_four_atr_and_a_new_cross(symbol, body, prior_alignment):
    m = pivot_market(
        "SHORT", symbol, open_price=98.3 + body, high_price=98.3 + body + .01,
        ma15_prev=99, ma5_prev=99 + prior_alignment,
    )
    result = MasterGateFunnel.evaluate_open_short(m)
    expected = body >= .8 and prior_alignment >= 0
    assert MasterGateFunnel.is_top_pivot_short(m) is expected
    assert result.can_execute is expected
    if expected:
        assert result.reason == "AUTHORIZED_SHORT:TRIGGER_TOP_PIVOT_CROSS"


def test_top_pivot_short_can_precede_kc_direction_change_but_not_flattening():
    m = pivot_market(
        "SHORT", open_price=99.1, high_price=99.11, ma5_prev=99.1, ma15_prev=99,
    )
    result = MasterGateFunnel.evaluate_open_short(m)
    assert result.can_execute
    assert result.details["trigger"] == "TRIGGER_TOP_PIVOT_CROSS"

    flat = m._replace(kc_middle_prev=m.kc_middle - 0.05 * m.atr + 1e-6)
    blocked = MasterGateFunnel.evaluate_open_short(flat)
    assert not blocked.can_execute
    assert blocked.reason == "BLOCKED_SHORT_KC_MIDDLE_TOO_FLAT"


def test_priority_cooldown_exemption_requires_a_valid_master_gate_entry():
    m = pivot_market(
        "SHORT", open_price=99.1, high_price=99.11, ma5_prev=99.1, ma15_prev=99,
    )
    assert MasterGateFunnel.has_valid_priority_entry(m, "SHORT")
    flat = m._replace(kc_middle=m.kc_middle_prev)
    assert not MasterGateFunnel.has_valid_priority_entry(flat, "SHORT")


@pytest.mark.parametrize(
    "bias_atr,allowed",
    [(0.999999, True), (1.0, True), (1.000001, False)],
)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ma15_extension_is_capped_at_one_atr_for_both_sides(bias_atr, allowed, side):
    sign = 1 if side == "LONG" else -1
    base = market(side)
    ma15 = base.ma15_curr
    close = ma15 + sign * bias_atr * base.atr
    opened = close - sign * 0.4 * base.atr
    middle = ma15
    m = base._replace(
        open_price=opened, close_price=close, quote=close,
        high_price=max(opened, close) + 0.05, low_price=min(opened, close) - 0.05,
        ma5_curr=ma15 + sign * 0.1 * base.atr,
        ma5_prev=ma15 + 0.05 * base.atr,
        ma15_prev=ma15, kc_upper=middle + 0.2, kc_middle=middle,
        kc_middle_prev=middle - sign * 0.2, kc_lower=middle - 0.2,
    )
    if side == "SHORT":
        m = m._replace(
            kc_middle=ma15 - 1.4 * base.atr,
            kc_middle_prev=ma15 - 1.2 * base.atr,
            kc_upper=ma15 - 0.6 * base.atr,
            kc_lower=close - 1.6 * base.atr,
        )
    else:
        m = m._replace(kc_upper=close + 1.6)
    result = (
        MasterGateFunnel.evaluate_open_long(m) if side == "LONG"
        else MasterGateFunnel.evaluate_open_short(m)
    )
    assert result.can_execute is allowed, result
    if not allowed:
        assert result.reason == f"BLOCKED_{side}_MA15_EXTENSION_1_ATR"


@pytest.mark.parametrize("fault", [
    "macro", "opposite_kc", "flat_ma5", "equal_ma15", "old_cross", "unclosed",
    "quote_middle", "quote_ma15", "chop", "bias_close", "bias_quote",
    "band_extension", "cooldown",
])
def test_top_pivot_short_exempts_only_flat_and_squeeze(fault):
    m = pivot_market(
        "SHORT", open_price=99.1, high_price=99.11, ma5_prev=99.1, ma15_prev=99,
    )
    assert MasterGateFunnel.evaluate_open_short(m).can_execute
    if fault == "macro":
        m = m._replace(macro_trend="BULLISH")
    elif fault == "opposite_kc":
        m = m._replace(kc_middle_prev=m.kc_middle - .01)
    elif fault == "flat_ma5":
        m = m._replace(ma5_prev=m.ma5_curr)
    elif fault == "equal_ma15":
        m = m._replace(ma5_curr=m.ma15_curr)
    elif fault == "old_cross":
        m = m._replace(ma5_prev=m.ma15_prev - .01)
    elif fault == "unclosed":
        m = m._replace(is_closed=False)
    elif fault == "quote_middle":
        m = m._replace(quote=m.kc_middle)
    elif fault == "quote_ma15":
        m = m._replace(quote=m.ma15_curr)
    elif fault == "chop":
        m = m._replace(ma_cross_count_8=3)
    elif fault == "bias_close":
        m = m._replace(close_price=m.ma15_curr - 1.5 * m.atr - .000001,
                       low_price=m.ma15_curr - 1.5 * m.atr - .01)
    elif fault == "bias_quote":
        m = m._replace(quote=m.ma15_curr - 1.5 * m.atr - .000001)
    elif fault == "band_extension":
        m = m._replace(kc_lower=99 - .01, kc_middle=99, kc_upper=99.2,
                       quote=99 - .01 - .8 * m.atr - .000001)
    # A priority channel still needs an aligned prior bar and current KC direction.
    expected = fault in ("cooldown", "macro", "quote_middle")
    assert MasterGateFunnel.evaluate_open_short(m, fault == "cooldown").can_execute is expected


def test_long_pullback_requires_existing_alignment_and_unbroken_middle():
    m = market(
        open_price=101.1, close_price=101.4, quote=101.4, low_price=101.0,
        kc_middle=101.0, kc_middle_prev=100.8, ma15_curr=101.0, ma15_prev=100.9,
        ma5_curr=101.2, ma5_prev=100.95,
    )
    assert MasterGateFunnel.evaluate_open_long(m).details["trigger"] == "TRIGGER_B_PULLBACK_KC_MIDDLE_BOUNCE"
    post_cross = MasterGateFunnel.evaluate_open_long(m._replace(ma5_prev=100.8))
    assert post_cross.can_execute
    assert post_cross.details["trigger"] == "TRIGGER_ALIGNED_TREND_SLOPES"
    shallower_retest = MasterGateFunnel.evaluate_open_long(m._replace(low_price=100.99))
    assert shallower_retest.can_execute
    assert shallower_retest.details["trigger"] == "TRIGGER_ALIGNED_TREND_SLOPES"


def test_long_early_ma15_cross_does_not_need_outside_rail():
    m = market(
        open_price=100.9, close_price=101.3, quote=101.3, low_price=100.8,
        kc_middle=101.0, kc_middle_prev=100.8, ma15_curr=101.0,
        ma5_curr=101.2, ma5_prev=101.1,
    )
    decision = MasterGateFunnel.evaluate_open_long(m)
    assert decision.can_execute and decision.details["trigger"] == "TRIGGER_C_EARLY_MA15_BREAKOUT"


@pytest.mark.parametrize("cross", ["middle", "ma15"])
def test_short_top_cross_modes_are_closed_and_downward(cross):
    m = market("SHORT")._replace(
        open_price=98.8, close_price=98.6, quote=98.6, high_price=99.1, low_price=98.5,
        kc_middle=98.9, kc_middle_prev=99.2, ma15_curr=99.0, ma15_prev=99.1,
        ma5_curr=98.6, ma5_prev=99.2 if cross == "middle" else 99.1,
    )
    decision = MasterGateFunnel.evaluate_open_short(m)
    assert decision.can_execute, decision
    assert decision.details["priority_channel"] == "TRIGGER_MA5_MA15_CROSS_WITH_TREND_SLOPE"


def test_short_rail_mode_requires_ma15_down_not_just_ma5_down():
    m = market("SHORT")._replace(
        open_price=98.8, close_price=98.5, quote=98.5, high_price=98.9, low_price=98.4,
        kc_lower=98.6, kc_middle=99.0, kc_middle_prev=99.2,
        ma15_curr=99.1, ma15_prev=99.2, ma5_curr=98.8, ma5_prev=98.9,
    )
    decision = MasterGateFunnel.evaluate_open_short(m)
    assert decision.can_execute and decision.details["trigger"] == "TRIGGER_C_BREAKDOWN_LOWER_RAIL"
    assert not MasterGateFunnel.evaluate_open_short(m._replace(ma15_prev=99.1)).can_execute


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_fresh_ma5_ma15_cross_is_blocked_but_established_alignment_passes(side):
    sign = 1 if side == "LONG" else -1
    base = market(side)
    middle = base.kc_middle - sign * 0.2
    aligned = base._replace(
        open_price=base.close_price - sign * .2,
        ma5_prev=base.ma15_prev + sign * .1,
        kc_middle=middle, kc_middle_prev=middle - sign * .2,
    )
    assert MasterGateFunnel.evaluate_entry_filters(aligned, side).can_execute

    crossed = aligned._replace(ma5_prev=aligned.ma15_prev - sign * .01)
    decision = MasterGateFunnel.evaluate_entry_filters(crossed, side)
    assert not decision.can_execute
    assert decision.reason == f"BLOCKED_{side}_MA5_MA15_FRESH_CROSS"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("weak_slope", ["kc", "ma5", "ma15"])
def test_fresh_ma_cross_requires_kc_and_both_ma_slopes_to_reach_threshold(side, weak_slope):
    sign = 1 if side == "LONG" else -1
    m = market(side)
    slope = 0.05 * m.atr
    ma15_prev = m.ma15_curr - sign * slope
    ma5_prev = ma15_prev - sign * 0.1
    kc_middle_prev = m.kc_middle - sign * slope
    if weak_slope == "kc":
        kc_middle_prev = m.kc_middle - sign * (slope - 0.001)
    elif weak_slope == "ma5":
        ma5_prev = m.ma15_curr + sign * 0.4 - sign * (slope - 0.001)
    else:
        ma15_prev = m.ma15_curr - sign * (slope - 0.001)
        ma5_prev = ma15_prev - sign * 0.1

    crossed = m._replace(
        open_price=m.close_price - sign * 0.5,
        ma5_curr=m.ma15_curr + sign * 0.4,
        ma5_prev=ma5_prev,
        ma15_prev=ma15_prev,
        kc_middle_prev=kc_middle_prev,
    )
    decision = (
        MasterGateFunnel.evaluate_open_long(crossed, True) if side == "LONG"
        else MasterGateFunnel.evaluate_open_short(crossed, True)
    )
    assert not decision.can_execute
    assert decision.reason == "BLOCKED_BY_POST_EXIT_COOLDOWN"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_fresh_ma_cross_with_expanding_kc_ma5_ma15_slopes_is_entry_and_cooldown_priority(side):
    sign = 1 if side == "LONG" else -1
    m = market(side)
    slope = 0.05 * m.atr
    ma15_prev = m.ma15_curr - sign * slope
    crossed = m._replace(
        open_price=m.close_price - sign * 0.5,
        ma5_curr=m.ma15_curr + sign * 0.4,
        ma5_prev=ma15_prev - sign * 0.1,
        ma15_prev=ma15_prev,
        kc_middle_prev=m.kc_middle - sign * slope,
    )
    decision = (
        MasterGateFunnel.evaluate_open_long(crossed, True) if side == "LONG"
        else MasterGateFunnel.evaluate_open_short(crossed, True)
    )
    assert decision.can_execute, decision
    assert decision.details["priority_channel"] == "TRIGGER_MA5_MA15_CROSS_WITH_TREND_SLOPE"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_post_cross_aligned_trend_slopes_authorize_entry_without_cooldown_exemption(side):
    sign = 1 if side == "LONG" else -1
    m = market(side)
    slope = 0.05 * m.atr
    trend = m._replace(
        open_price=m.close_price - sign * 0.5,
        ma5_curr=m.ma15_curr + sign * 0.4,
        ma5_prev=m.ma15_curr + sign * 0.2,
        ma15_prev=m.ma15_curr - sign * slope,
        kc_middle_prev=m.kc_middle - sign * slope,
    )
    decision = (
        MasterGateFunnel.evaluate_open_long(trend) if side == "LONG"
        else MasterGateFunnel.evaluate_open_short(trend)
    )
    assert decision.can_execute, decision
    assert decision.details["trigger"] == "TRIGGER_ALIGNED_TREND_SLOPES"

    cooling = (
        MasterGateFunnel.evaluate_open_long(trend, True) if side == "LONG"
        else MasterGateFunnel.evaluate_open_short(trend, True)
    )
    assert not cooling.can_execute
    assert cooling.reason == "BLOCKED_BY_POST_EXIT_COOLDOWN"


@pytest.mark.parametrize(
    "held_side,market_side,target_side,trigger,body",
    [
        ("SHORT", "LONG", "LONG", "TRIGGER_BOTTOM_PIVOT_CROSS", 1.0),
        ("LONG", "SHORT", "SHORT", "TRIGGER_TOP_PIVOT_CROSS", 0.8),
    ],
)
def test_position_flip_requires_closed_directional_pivot(
    held_side, market_side, target_side, trigger, body,
):
    m = pivot_market(
        market_side,
        open_price=(101.7 - body if market_side == "LONG" else 98.3 + body),
    )
    if market_side == "LONG":
        m = m._replace(low_price=100.7, high_price=101.71)
    sign = 1 if market_side == "LONG" else -1
    candidate = MasterGateFunnel.evaluate_position_flip(m, held_side)
    assert candidate == (target_side, trigger)

    entry = (
        MasterGateFunnel.evaluate_open_long(m, is_in_cooldown=True)
        if target_side == "LONG"
        else MasterGateFunnel.evaluate_open_short(m, is_in_cooldown=True)
    )
    assert entry.can_execute, entry.reason
    assert entry.details["priority_channel"] == trigger

    assert MasterGateFunnel.evaluate_position_flip(
        m._replace(is_closed=False), held_side,
    ) is None
    assert MasterGateFunnel.evaluate_position_flip(
        m._replace(open_price=m.close_price - sign * (body - 0.01)), held_side,
    ) is None


@pytest.mark.parametrize("already_open", [False, True])
def test_execution_manager_flip_waits_for_flat_then_uses_shared_open_path(
    already_open, monkeypatch,
):
    from core.services.execution_manager import ExecutionManager
    from core.gates.master_gate_funnel import MasterGateFunnel

    frame = early_frame("SHORT")
    previous, current = frame.index[-2], frame.index[-1]
    frame.loc[previous, ["ma5", "ma15", "kc_middle", "atr"]] = [
        100.0, 99.9, 98.1, 2.0,
    ]
    frame.loc[current, [
        "open", "high", "low", "close", "ma5", "ma15",
        "kc_middle", "kc_upper", "kc_lower",
    ]] = [99.1, 99.11, 98.29, 98.3, 98.8, 99.0, 98.0, 98.6, 97.5]
    account = SimpleNamespace(
        id="flip-test",
        positions={"1000PEPE/USDT": {"side": "LONG"}} if already_open else {},
    )
    async def open_position(**kwargs):
        account.positions[kwargs["symbol"]] = {"side": kwargs["side"]}
        return True

    open_mock = AsyncMock(side_effect=open_position)
    monkeypatch.setattr(ExecutionManager, "open_position", open_mock)
    monkeypatch.setattr(ExecutionManager, "_position_flip_locks", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    monkeypatch.setattr(
        MasterGateFunnel,
        "evaluate_open_short",
        lambda snapshot, is_in_cooldown=False: SimpleNamespace(
            can_execute=True, reason="PASS_TEST_GATE",
        ),
    )
    monkeypatch.setattr(
        MasterGateFunnel,
        "pivot_kc_location_veto",
        lambda snapshot, side: None,
    )

    result = asyncio.run(ExecutionManager._try_structure_position_flip(
        account=account,
        symbol="1000PEPE/USDT",
        position_side="LONG",
        frame=frame,
        quote=98.8,
        macro_trend=None,
    ))
    if already_open:
        assert result["reason"] == "POSITION_NOT_FLAT"
        open_mock.assert_not_awaited()
    else:
        assert result == {
            "success": True, "side": "SHORT",
            "reason": "TRIGGER_TOP_PIVOT_CROSS",
        }
        open_mock.assert_awaited_once()
        call = open_mock.await_args.kwargs
        assert call["side"] == "SHORT"
        assert call["meta"]["entry_pattern"] == "CROSSOVER"
        assert call["meta"]["structure_confirmation"] == "MA_CROSSOVER"
        assert "upstream_entry_gate_authorized" not in call["meta"]
        assert "no_rigid_sl" not in call["meta"]


def test_execution_manager_flip_honors_strict_kc_location_veto(monkeypatch):
    from core.services.execution_manager import ExecutionManager
    from core.gates.master_gate_funnel import MasterGateFunnel

    account = SimpleNamespace(id="flip-location-test", positions={})
    open_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(ExecutionManager, "open_position", open_mock)
    monkeypatch.setattr(
        MasterGateFunnel,
        "pivot_kc_location_veto",
        lambda snapshot, side: "VETO_SHORT_PIVOT_KC_POSITION",
    )
    frame = early_frame("SHORT")
    previous, current = frame.index[-2], frame.index[-1]
    frame.loc[previous, ["ma5", "ma15", "kc_middle", "atr"]] = [
        100.0, 99.9, 98.1, 2.0,
    ]
    frame.loc[current, [
        "open", "high", "low", "close", "ma5", "ma15",
        "kc_middle", "kc_upper", "kc_lower",
    ]] = [99.1, 99.11, 98.29, 98.3, 98.8, 99.0, 98.0, 98.6, 97.5]
    current = frame.index[-1]
    frame.loc[current, "is_closed"] = True
    result = asyncio.run(ExecutionManager._try_structure_position_flip(
        account=account,
        symbol="1000PEPE/USDT",
        position_side="LONG",
        frame=frame,
        quote=float(frame.loc[current, "close"]),
        macro_trend=None,
    ))
    assert result == {
        "success": False,
        "side": "SHORT",
        "reason": "VETO_SHORT_PIVOT_KC_POSITION",
    }
    open_mock.assert_not_awaited()


@pytest.mark.parametrize(
    "target_side,reason",
    [
        ("SHORT", "VETO_SHORT_PIVOT_KC_POSITION"),
        ("LONG", "VETO_LONG_PIVOT_KC_POSITION"),
    ],
)
def test_reversal_entry_obeys_strict_macro_kc_location(target_side, reason):
    m = market(target_side)
    assert MasterGateFunnel.pivot_kc_location_veto(m, target_side) == reason


@pytest.mark.parametrize("minute,opened,close,high,low,ma5,prev_ma5,ma15,middle,prev_middle,lower,atr,allowed", [
    # 07:17 is already below the strict upper-channel pivot zone, so it is a late short.
    ("07:17", .004767, .0047607, .0047671, .0047588, .00476464, .00476716, .004765926666666667,
     .00476838478130929, .004769193705657637, .004758570495595004, .000006642857142857157, False),
    # 07:18 exceeds the one-ATR MA15 extension cap and is vetoed despite the breakdown body.
    ("07:18", .0047608, .0047528, .0047637, .0047528, .00476202, .00476464, .004765033333333334,
     .004766900516422691, .00476838478130929, .004756539802136976, .000006542857142857193, False),
    # 07:26 opens only ~0.07 ATR below the lower rail: Owner 2026-11-03 relaxed anti-bottom
    # allows a big-body breakdown opening up to 0.25 ATR beyond the band.
    ("07:26", .0047489, .0047446, .004751, .0047419, .00474988, .00475068, .004757186666666666,
     .004757829815772929, .004759222427959553, .004748465530058643, .000006035714285714375, False),
])
def test_historical_pepe_microstructure_is_not_historical_fill_proof(
    minute, opened, close, high, low, ma5, prev_ma5, ma15, middle, prev_middle, lower, atr, allowed,
):
    # Chart OHLC/indicator evidence; historical runtime macro/quote was not recovered.
    m = MarketSnapshot(
        symbol="1000PEPE/USDT", quote=close, open_price=opened, close_price=close,
        high_price=high, low_price=low,
        ma3_curr=0, ma3_prev=0, ma5_curr=ma5, ma5_prev=prev_ma5,
        ma15_curr=ma15, ma15_prev=ma15, kc_upper=middle + (middle - lower),
        kc_middle=middle, kc_middle_prev=prev_middle, kc_lower=lower,
        atr=atr, macro_trend="NEUTRAL", ma_cross_count_8=0, is_closed=True,
    )
    decision = MasterGateFunnel.evaluate_open_short(m)
    assert decision.can_execute is allowed, (minute, decision)
    macro_conflict = MasterGateFunnel.evaluate_open_short(m._replace(macro_trend="BULLISH"))
    assert macro_conflict.can_execute is allowed


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("boundary", [-.001, 0, .001])
def test_priority_entry_bypasses_post_close_cooldown(symbol, side, boundary, monkeypatch):
    now = math.floor(time.time() / 60) * 60
    monkeypatch.setattr(time, "time", lambda: now + boundary)
    frame = early_frame(side)
    last, previous = frame.index[-1], frame.index[-2]
    if side == "LONG":
        frame.loc[last, ["open", "close", "high", "low", "kc_lower", "kc_middle", "kc_upper",
                         "ma15", "ma5"]] = [
            100.7, 101.5, 101.51, 100.69, 101.2, 101.8, 102.4, 100.9, 101.2,
        ]
        frame.loc[previous, ["kc_middle", "ma5", "ma15"]] = [101.69, 100.89, 100.9]
    else:
        frame.loc[last, ["open", "close", "high", "low", "kc_lower", "kc_middle", "kc_upper",
                         "ma15", "ma5"]] = [
            99.3, 98.5, 99.31, 98.49, 97.6, 98.2, 98.8, 99.1, 97.9,
        ]
        frame.loc[previous, ["kc_middle", "ma5", "ma15"]] = [98.31, 99.11, 99.1]
    # Close was inside its minute; the partial closing candle does not count.
    closed_at = now - 180 + 30
    result = evaluate_meme_entry_frame(
        frame, symbol, side, frame.iloc[-1]["close"], last_closed_at=closed_at,
    )
    assert result.can_execute, result


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_new_authority_accepts_already_crossed_middle_not_late_chase(symbol, side):
    frame = early_frame(side)
    sign = 1 if side == "LONG" else -1
    last = frame.index[-1]
    opened = frame.iloc[-1]["kc_middle"] + sign * .02
    close = opened + sign * 1.7
    frame.loc[last, ["open", "close", "high", "low"]] = [
        opened, close, max(opened, close) + .01, min(opened, close) - .01,
    ]
    frame.loc[last, ["ma15", "ma5"]] = [close - sign * .8, close - sign * .5]
    quote = float(close)
    signal = evaluate_entry_method(frame, symbol=symbol, quote=quote, expected_method=MEME_GATE_ENTRY)
    assert signal is not None
    assert revalidate_entry_method(frame, symbol=symbol, side=side, quote=quote, context=method_context(signal))[0]
    late = float(frame.iloc[-1]["ma15"]) + sign * 4.01
    assert not revalidate_entry_method(frame, symbol=symbol, side=side, quote=late, context=method_context(signal))[0]


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_independent_outer_pivot_no_longer_bypasses_shared_meme_filters(symbol, side):
    from test_kc_outer_pivot_entry import market as outer_market
    from core.services.entry_methods import KC_OUTER_PIVOT_REVERSAL

    frame = outer_market(side)
    frame.attrs["macro_trend"] = "BEARISH" if side == "LONG" else "BULLISH"
    quote = float(frame.iloc[-1]["close"])
    assert not evaluate_meme_entry_frame(frame, symbol, side, quote).can_execute
    assert evaluate_entry_method(
        frame, symbol=symbol, quote=quote, expected_method=KC_OUTER_PIVOT_REVERSAL,
    ) is None


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side,mode,fresh_fault", [
    ("LONG", "expansion", None), ("SHORT", "expansion", None),
    ("LONG", "early", None), ("LONG", "early_equal", None),
    ("LONG", "bottom_v", None), ("LONG", "bottom_v", "stale_ma5"),
    ("SHORT", "top_cross", None), ("SHORT", "top_cross", "stale_ma5"),
    ("SHORT", "top_cross", "bias"),
])
@pytest.mark.parametrize("flat", [False, True])
@pytest.mark.parametrize("account_type", ["paper", "testnet"])
def test_real_scan_pipeline_reaches_new_authority_before_legacy_chop(
    symbol, side, mode, fresh_fault, flat, account_type, tmp_path, monkeypatch,
):
    from core.paper_account import PaperAccount
    from core.services.execution_manager import ExecutionManager
    from core.services.data_pipeline import DataPipeline

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr("core.services.order_audit_trail.AUDIT_LOG_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {})
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    frame = early_frame(side)
    sign = 1 if side == "LONG" else -1
    last, prev = frame.index[-1], frame.index[-2]
    body = 1.7 if mode == "expansion" else (1.0 if mode == "bottom_v" else .8)
    opened = float(frame.iloc[-1]["close"]) - sign * body
    frame.loc[last, ["open", "low", "high"]] = [
        opened, min(opened, frame.iloc[-1]["close"]) - .01,
        max(opened, frame.iloc[-1]["close"]) + .01,
    ]
    if flat:
        frame.loc[prev, "kc_middle"] = frame.loc[last, "kc_middle"]
    if mode == "early_equal":
        frame.loc[last, "ma5"] = frame.loc[last, "ma15"]
    if mode in ("top_cross", "bottom_v"):
        frame.loc[prev, "ma5"] = frame.loc[prev, "ma15"] + (
            .01 if side == "SHORT" else -.01
        )
    if mode == "top_cross":
        frame.loc[last, ["kc_lower", "kc_middle", "kc_upper"]] = [97.5, 98.1, 98.7]
        frame.loc[prev, "kc_middle"] = 98.21
        frame.loc[last, "ma15"] = 99.05
    elif mode == "bottom_v":
        frame.loc[last, ["kc_lower", "kc_middle", "kc_upper"]] = [101.2, 101.8, 102.4]
        frame.loc[prev, "kc_middle"] = 101.69
        frame.loc[last, "ma15"] = 100.95
    if flat:
        frame.loc[prev, "kc_middle"] = frame.loc[last, "kc_middle"]
    if mode != "early_equal":
        frame.loc[last, "ma5"] = frame.loc[last, "kc_middle"] + sign * .3
    quote = float(frame.iloc[-1]["close"])
    signal = evaluate_entry_method(frame, quote=quote, symbol=symbol, expected_method=MEME_GATE_ENTRY)
    if flat:
        # Owner 2026-11-03 swing filter: a flat KC middle blocks every channel.
        assert signal is None
        return
    if mode == "early_equal":
        assert signal is None
        return
    assert signal is not None
    if mode == "bottom_v":
        assert signal["trigger"] == "TRIGGER_BOTTOM_PIVOT_CROSS"
    elif mode == "top_cross":
        assert signal["trigger"] == "TRIGGER_TOP_PIVOT_CROSS"
    elif mode != "expansion":
        assert signal["trigger"] == "TRIGGER_EARLY_0748_BREAKOUT"
    if account_type == "paper":
        account = PaperAccount()
    else:
        from core.testnet_account import BinanceTestnetAccount
        from test_testnet_account import FakeTestnetExchange
        from decimal import Decimal, ROUND_DOWN
        monkeypatch.setattr("core.testnet_account.STATE_FILE", str(tmp_path / "testnet.json"))
        monkeypatch.setattr(BinanceTestnetAccount, "credentials_configured", staticmethod(lambda: True))
        exchange = FakeTestnetExchange()
        exchange.amount_to_precision = lambda _symbol, value: str(
            Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
        )
        account = BinanceTestnetAccount(exchange)
        asyncio.run(account.initialize())
    account.balance = account.available_balance = 150
    fresh_frame = frame.copy()
    if fresh_fault == "stale_ma5":
        fresh_frame.loc[last, "ma5"] = fresh_frame.loc[prev, "ma5"]
    elif fresh_fault == "bias":
        fresh_frame.loc[last, "ma15"] = quote + 4.01
    account.entry_frame_provider = lambda *_a, **_k: fresh_frame
    snapshot = DataPipeline.calculate_snapshot(symbol, frame, current_price=quote)
    snapshot.raw_df = frame
    snapshot.curr_k, snapshot.prev_k = frame.iloc[-1].to_dict(), frame.iloc[-2].to_dict()
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *_a, **_k: snapshot))
    result = asyncio.run(ExecutionManager.process_symbol_pipeline(symbol, account, klines_df=frame, quote=quote))
    if fresh_fault:
        assert result["status"] != "ENTRY_EXECUTED"
        assert symbol not in account.positions
        assert not account.trades
        return
    assert result["status"] == "ENTRY_EXECUTED", "\n".join(str(x.get("text")) for x in account.logs[-10:])
    assert account.positions[symbol]["entry_method_authority"] == MEME_GATE_ENTRY


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("held", [0, 1, 3, 4, 5, 8])
@pytest.mark.parametrize("fault", [None, "buffer", "ma", "color", "pivot", "net"])
def test_master_exit_never_uses_hard_loss_or_trailing_without_all_qualifiers(side, held, fault, caplog):
    m = market(side)
    sign = 1 if side == "LONG" else -1
    close = m.kc_middle - sign * 1.1
    m = m._replace(close_price=close, open_price=close + sign * .2,
                   ma5_curr=m.ma15_curr - sign * .1)
    p = PositionSnapshot("CAP/USDT", side, 100, 60_000, held, 99, True,
                         lowest_price_since_entry=1, highest_price_since_entry=999,
                         estimated_net_pnl=1)
    if fault == "buffer":
        m = m._replace(close_price=m.kc_middle - sign * .9)
    elif fault == "ma":
        m = m._replace(ma5_curr=m.ma15_curr)
    elif fault == "color":
        m = m._replace(open_price=m.close_price - sign * .1)
    elif fault == "pivot":
        p = p._replace(has_confirmed_pivot=False)
    elif fault == "net":
        p = p._replace(estimated_net_pnl=0)
    caplog.set_level("INFO")
    result = MasterGateFunnel.evaluate_position_exit(p, m, "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", 600_000)
    assert result.can_execute is (held >= 5 and fault is None)
    if not result.can_execute:
        assert f"[VETO_HOLD_{side}]" in caplog.text
        if side == "SHORT":
            assert "[UI_DISPLAY: 續抱]" in caplog.text


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reason", [
    "OPPOSITE_KC_BAND_BREACH", "EXIT_BY_TWO_CLOSED_ADVERSE_ABNORMAL",
    "CIRCUIT_BREAKER_HARD_SL", "EXIT_LONG_AT_PEAK",
    "EXIT_BY_EXTREME_WATERFALL_ACCOUNT_HARD_STOP",
    "EXIT_BY_EXTREME_WATERFALL_OPPOSITE_KC_BAND_BREACH",
    "EXIT_BY_TWO_CLOSED_ADVERSE_ABNORMAL_ACCOUNT_CIRCUIT_BREAKER",
])
def test_disabled_disasters_cannot_reach_account_boundary(symbol, side, reason, monkeypatch):
    pos = dict(symbol=symbol, side=side, entry_price=100, qty=1, open_timestamp=time.time())
    monkeypatch.setattr(
        "core.services.math_execution_gate.validate_exit_math",
        lambda *_a, **_k: (True, "FORGED_DISASTER", {}),
    )
    assert not automated_position_exit_allowed(symbol, pos, {}, reason, 90)
    assert not validate_exit_at_execution(SimpleNamespace(position_meta={}), symbol, pos, 90, reason)[0]


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_disabled_disasters_fail_math_even_with_large_price_loss(symbol, side):
    pos = dict(symbol=symbol, side=side, entry_price=100, qty=1, open_timestamp=time.time())
    assert not validate_exit_math(pos, None, 80 if side == "LONG" else 120, close_reason="EXIT_BY_EXTREME_WATERFALL")[0]
    assert automated_position_exit_allowed(symbol, pos, {}, "手動平倉", 90, is_manual=True)


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("account_type", ["paper", "testnet"])
@pytest.mark.parametrize("breached", [False, True])
def test_only_math_verified_waterfall_may_close_entry_candle(
    symbol, side, account_type, breached, tmp_path, monkeypatch,
):
    from core.paper_account import PaperAccount
    from core.testnet_account import BinanceTestnetAccount
    from test_testnet_account import FakeTestnetExchange
    from core.gates.unified_pipeline_gates import ExitGate

    now = math.floor(time.time() / 60) * 60
    monkeypatch.setattr(time, "time", lambda: now + 30)
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    if account_type == "paper":
        account = PaperAccount()
    else:
        account = BinanceTestnetAccount(FakeTestnetExchange(), state_file=str(tmp_path / "testnet.json"))
        account.refresh = AsyncMock()
    pos = dict(symbol=symbol, side=side, entry_price=100, qty=1, margin=20,
               leverage=5, open_timestamp=now + 1)
    quote = (88 if breached else 99) if side == "LONG" else (112 if breached else 101)
    frame = pd.DataFrame([
        dict(timestamp=(now - 60) * 1000, open=100, close=100, high=101, low=99,
             ma5=100, ma15=100, atr=1, kc_middle=100, kc_upper=110, kc_lower=90, is_closed=True),
        dict(timestamp=now * 1000, open=100, close=quote, high=max(100, quote),
             low=min(100, quote), ma5=100, ma15=100, atr=1, kc_middle=100,
             kc_upper=110, kc_lower=90, is_closed=False),
    ])
    frame.attrs.update(timeframe_ms=60_000, snapshot_ms=(now + 30) * 1000)
    account.positions[symbol] = pos
    account.position_meta[symbol] = dict(pos)
    account._channel_exit_frames = {symbol: frame}
    allowed, code, _ = ExitGate.evaluate(pos, dict(
        symbol=symbol, raw_df=frame, current_price=quote, kc_upper=110,
        kc_lower=90, kc_middle=100, atr=1, macro_trend="NEUTRAL",
    ))
    assert allowed is breached
    if breached:
        assert code == "EXIT_BY_EXTREME_WATERFALL"
    assert UltimateExitGatekeeper.can_execute_exit(
        symbol, pos, {}, "EXIT_BY_EXTREME_WATERFALL", quote,
        frame=frame, is_bar_closed=False,
    ) is breached
    assert asyncio.run(account.close_position(symbol, quote, "EXIT_BY_EXTREME_WATERFALL")) is breached
    assert (symbol not in account.positions) is breached
    assert len(account.trades) == int(breached)


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", ["stale", "future", "missing"])
def test_opposite_rail_cannot_use_an_unverified_candle_time(symbol, side, fault):
    now = math.floor(time.time() / 60) * 60
    timestamp = {"stale": now - 300, "future": now + 60, "missing": None}[fault]
    frame = pd.DataFrame([
        dict(timestamp=(now - 60) * 1000, close=100, atr=1, kc_upper=110, kc_lower=90),
        dict(timestamp=timestamp * 1000 if timestamp is not None else None,
             close=100, atr=1, kc_upper=110, kc_lower=90),
    ])
    position = dict(symbol=symbol, side=side, entry_price=100, qty=1)
    allowed, reason, _ = validate_exit_math(
        position, frame, 88 if side == "LONG" else 112,
        close_reason="OPPOSITE_KC_BAND_BREACH", now_ms=(now + 30) * 1000,
    )
    assert not allowed
    assert reason == "BLOCKED_MEME_STRATEGY_DISASTER_DISABLED"


def test_new_meme_filters_are_not_applied_to_the_independent_wait_route(monkeypatch):
    from core.gates.central_entry_gate import CentralEntryGate

    def unexpected_meme_filter(*_args, **_kwargs):
        pytest.fail("The new MEME qualification must not change WAIT semantics")

    monkeypatch.setattr(
        "core.gates.master_gate_funnel.evaluate_meme_entry_frame", unexpected_meme_filter,
    )
    frame = early_frame("SHORT")
    CentralEntryGate.verify_and_authorize(
        symbol="CAP/USDT", side="SHORT", price=float(frame.iloc[-2]["close"]),
        klines_or_snapshot=frame, source="WAIT_1M",
    )


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", [None, "unclosed", "four_bars", "buffer", "ma", "color", "pivot", "net"])
def test_ultimate_exit_gate_uses_closed_structure_not_a_caller_success_flag(symbol, side, fault):
    from test_profit_pivot_exit import pivot_frame
    from test_kc_wave_exit import position

    frame = pivot_frame(side)
    sign = 1 if side == "LONG" else -1
    pos = position(side, frame)
    pos["symbol"] = symbol
    last = frame.index[-1]
    quote = float(frame.loc[last, "close"])
    if fault == "unclosed":
        frame.loc[last, "is_closed"] = False
    elif fault == "four_bars":
        pos["open_timestamp"] = float(frame.iloc[-4]["timestamp"]) / 1000
    elif fault == "buffer":
        frame.loc[last, "kc_middle"] = quote + sign * .5
    elif fault == "ma":
        frame.loc[last, "ma5"] = frame.loc[last, "ma15"]
    elif fault == "color":
        frame.loc[last, "open"] = quote - sign * .01
    elif fault == "pivot":
        frame.loc[last, "high" if side == "LONG" else "low"] = frame.iloc[-2]["high" if side == "LONG" else "low"]
    elif fault == "net":
        quote = pos["entry_price"]
    reason = "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT" if side == "LONG" else "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
    assert UltimateExitGatekeeper.can_execute_exit(
        symbol, pos, {}, reason, quote, frame=frame, is_bar_closed=True,
    ) is (fault is None)
    assert not UltimateExitGatekeeper.can_execute_exit(
        symbol, pos, {}, reason, quote, frame=frame, is_bar_closed=False,
    )


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("elapsed,held", [
    (0, 0), (59, 0), (60, 0), (180, 2), (358, 4), (359, 5),
])
def test_intrabar_fill_counts_only_fully_held_candles_not_cached_counter(
    symbol, side, elapsed, held, monkeypatch,
):
    from datetime import datetime, timezone
    from test_profit_pivot_exit import pivot_frame
    from test_kc_wave_exit import position
    from core.services.exits.strict_true_pivot import evaluate_position_exit

    opened = datetime(2026, 11, 3, 1, 32, 1, tzinfo=timezone.utc).timestamp()
    now = opened + elapsed
    monkeypatch.setattr(time, "time", lambda: now)
    frame = pivot_frame(side)
    pos = {**position(side, frame), "symbol": symbol,
           "open_timestamp": opened, "bars_held": 999}
    allowed, reason, details = evaluate_position_exit(pos, frame)
    assert details["bars_held"] == held
    assert allowed is (held >= 5)
    if held < 5:
        assert reason == "VETO_HOLD_MIN_5_COMPLETED_BARS"
    exit_reason = (
        "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT" if side == "LONG"
        else "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
    )
    assert UltimateExitGatekeeper.can_execute_exit(
        symbol, pos, {"bars_held": 999}, exit_reason,
        float(frame.iloc[-1]["close"]), frame=frame, is_bar_closed=True,
    ) is (held >= 5)
    assert not UltimateExitGatekeeper.can_execute_exit(
        symbol, pos, {}, "EXIT_BY_TWO_CLOSED_ADVERSE_ABNORMAL",
        float(frame.iloc[-1]["close"]), frame=frame, is_bar_closed=True,
    )


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reason", [
    "EXIT_BY_PULLBACK", "EXIT_BY_EXTREME_WATERFALL",
    "HARD_STOP_LOSS", "AUTHORIZED_CLOSE_SHORT", "EXIT_BY_TWO_CLOSED_ADVERSE_ABNORMAL",
])
def test_main_execution_discards_veto_before_calling_even_an_unguarded_account(symbol, side, reason):
    from core.services.execution_manager import ExecutionManager
    from test_profit_pivot_exit import pivot_frame
    from test_kc_wave_exit import position

    frame = pivot_frame(side)
    pos = position(side, frame)
    pos["symbol"] = symbol
    account = SimpleNamespace(
        positions={symbol: pos}, position_meta={}, close_position=AsyncMock(return_value=True),
    )
    assert not asyncio.run(ExecutionManager.close_position(
        account, symbol, float(frame.iloc[-1]["close"]), reason, meta={"raw_df": frame},
    ))
    account.close_position.assert_not_awaited()
    assert symbol in account.positions


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("account_type", ["paper", "testnet"])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("fault", ["ultimate_veto", "refresh_failure"])
def test_full_and_partial_account_api_never_submit_after_ultimate_veto(
    symbol, side, account_type, partial, fault, tmp_path, monkeypatch,
):
    from core.paper_account import PaperAccount
    from core.testnet_account import BinanceTestnetAccount
    from test_testnet_account import FakeTestnetExchange
    from test_profit_pivot_exit import pivot_frame
    from test_kc_wave_exit import position

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    frame = pivot_frame(side)
    pos = position(side, frame)
    pos["symbol"] = symbol
    if account_type == "paper":
        account = PaperAccount()
    else:
        exchange = FakeTestnetExchange()
        account = BinanceTestnetAccount(exchange, state_file=str(tmp_path / "testnet.json"))
        exchange.create_order = AsyncMock()
        account.refresh = AsyncMock()
    account.positions[symbol] = pos
    account.position_meta[symbol] = dict(pos)
    account._channel_exit_frames = {symbol: frame}
    if fault == "ultimate_veto":
        monkeypatch.setattr(
            "core.services.math_execution_gate.validate_exit_at_execution",
            lambda *_a, **_k: (True, "TEST_UPSTREAM_APPROVED", {}),
        )
        monkeypatch.setattr(UltimateExitGatekeeper, "can_execute_exit", staticmethod(lambda *_a, **_k: False))
    else:
        # Upstream math gate approves so the frame refresh path itself is exercised.
        monkeypatch.setattr(
            "core.services.math_execution_gate.validate_exit_at_execution",
            lambda *_a, **_k: (True, "TEST_UPSTREAM_APPROVED", {}),
        )
        def fail_refresh(_symbol):
            raise RuntimeError("test market refresh failed")
        account.entry_frame_provider = fail_refresh
    reason = "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT" if side == "LONG" else "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
    close = account.partial_close_position if partial else account.close_position
    assert not asyncio.run(close(symbol, float(frame.iloc[-1]["close"]), reason))
    assert account.positions[symbol]["qty"] == pos["qty"]
    assert not account.trades
    assert not account.closing_lock
    from core.services.exits.strict_true_pivot import PROFIT_PIVOT_PENDING_KEY
    assert PROFIT_PIVOT_PENDING_KEY not in account.positions[symbol]
    assert PROFIT_PIVOT_PENDING_KEY not in account.position_meta[symbol]
    if account_type == "testnet":
        exchange.create_order.assert_not_awaited()
    if fault == "refresh_failure":
        assert any("ULTIMATE_EXIT_FRAME_REFRESH_FAILED" in log.get("text", "") for log in account.logs)


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("ratio,squeezed", [(.20, True), (.25, True), (.2501, False), (.60, False)])
def test_meme_squeeze_is_relative_to_twenty_bar_widest_channel(symbol, ratio, squeezed):
    from core.gates.master_gate_funnel import MarketSnapshot
    # 1m MEME channels are < 1% of price: only the relative 25% rule may veto.
    width = 0.3
    m = MarketSnapshot(
        symbol, 100.0, 100.0, 100.0, 100.05, 99.95, 0, 0, 100.0, 100.0, 100.0, 100.0,
        100.0 + width / 2, 100.0, 99.9, 100.0 - width / 2, 1.0, "NEUTRAL", 0,
        is_closed=True, kc_width_max_20=width / ratio,
    )
    blocked, reason = MasterGateFunnel.evaluate_market_blacklist(m)
    assert (reason == "VETO_NARROW_SQUEEZE_RANGE") is squeezed
    legacy = m._replace(kc_width_max_20=0.0)
    assert MasterGateFunnel.evaluate_market_blacklist(legacy)[1] == "VETO_NARROW_SQUEEZE_RANGE"
