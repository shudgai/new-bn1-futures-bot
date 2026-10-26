import pandas as pd
import pytest

from core.gates.holding_protection_gate import HoldingProtectionExitGate
from core.services.exits.abnormal_candle_exit import (
    two_closed_adverse_abnormal_evidence,
)
from core.services.math_execution_gate import validate_exit_math
from core.services.swing_pivot_rules import (
    evaluate_outer_rail_reversal_exit,
    evaluate_swing_pivot_entry,
)


def _position(side):
    return {
        "side": side,
        "entry_mode": "CHANNEL_SWING",
        "entry_price": 100.0,
        "qty": 1.0,
        "margin": 100.0,
        "leverage": 1.0,
        "open_timestamp": 100.0,
    }


def _frame(side, body=0.5, *, live=False):
    rows = []
    for index, stamp in enumerate((60_000, 120_000, 180_000, 240_000)):
        opening = 100.0
        closing = opening - body if side == "LONG" and index >= 2 else opening
        if side == "SHORT" and index >= 2:
            closing = opening + body
        rows.append({
            "timestamp": stamp,
            "is_closed": not (live and index == 3),
            "open": opening,
            "high": max(opening, closing) + 0.1,
            "low": min(opening, closing) - 0.1,
            "close": closing,
            "atr": 1.0,
            "ma5": 100.0,
            "ma15": 100.0,
            "kc_upper": 110.0,
            "kc_middle": 100.0,
            "kc_lower": 90.0,
        })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("body,expected", [(0.499, False), (0.5, True), (0.501, True)])
def test_two_closed_adverse_candles_have_exact_half_atr_threshold(side, body, expected):
    evidence = two_closed_adverse_abnormal_evidence(_position(side), _frame(side, body))
    assert (evidence is not None) is expected


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_two_closed_abnormal_candles_pass_final_math_and_holding_gates(side):
    position = _position(side)
    frame = _frame(side, 0.5)
    reason = "EXIT_BY_TWO_CLOSED_ADVERSE_ABNORMAL"
    allowed, code, evidence = validate_exit_math(
        position, frame, 99.0 if side == "LONG" else 101.0,
        close_reason=reason,
    )
    assert allowed, code
    assert evidence["exit_type"] == "TWO_CLOSED_ADVERSE_ABNORMAL"

    allowed, code, evidence = HoldingProtectionExitGate.validate_exit(
        position, frame, 99.0 if side == "LONG" else 101.0, reason,
    )
    assert allowed, code
    assert evidence["body_atr_multiple"] == 0.5


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("body,expected", [(1.499, False), (1.5, True), (1.501, True)])
def test_single_live_waterfall_requires_15_atr_and_midline_cross(side, body, expected):
    frame = _frame(side, 0.0, live=True)
    frame.loc[3, "open"] = 100.0
    frame.loc[3, "close"] = 100.0
    frame.loc[3, "kc_middle"] = 99.0 if side == "LONG" else 101.0
    quote = 100.0 - body if side == "LONG" else 100.0 + body
    allowed, code, details = HoldingProtectionExitGate.validate_exit(
        _position(side), frame, quote, "EXIT_BY_EXTREME_WATERFALL",
    )
    assert allowed is expected, f"{code}: {details}"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_swing_pivot_entry_requires_three_closed_points_and_uses_pivot_stop(side):
    left = dict(timestamp=60_000, is_closed=True, open=100., high=101., low=99., close=100.,
                atr=1.)
    if side == "LONG":
        pivot = dict(timestamp=120_000, is_closed=True, open=99., high=100., low=98., close=98.5,
                     atr=1.)
        curr = dict(timestamp=180_000, is_closed=True, open=99., high=101., low=98.5, close=100.,
                    atr=1.)
        expected_stop = 98.
    else:
        pivot = dict(timestamp=120_000, is_closed=True, open=101., high=102., low=99., close=101.5,
                     atr=1.)
        curr = dict(timestamp=180_000, is_closed=True, open=101., high=101.5, low=99., close=100.,
                    atr=1.)
        expected_stop = 102.
    frame = pd.DataFrame([left, pivot, curr])
    frame.attrs["timeframe_ms"] = 60_000
    signal = evaluate_swing_pivot_entry(frame)
    assert signal and signal["side"] == side
    assert signal["entry_type"] == "SWING_PIVOT"
    assert signal["stop_loss_price"] == expected_stop
    assert signal["pivot_bar_timestamp"] == 120_000
    unclosed = frame.copy()
    unclosed.loc[unclosed.index[-1], "is_closed"] = False
    assert evaluate_swing_pivot_entry(unclosed) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_outer_rail_exhaustion_exit_requires_closed_confirmation(side):
    rows = [
        dict(timestamp=60_000, is_closed=True, open=100., high=101., low=99., close=100., atr=1.,
             ma3=100., kc_upper=105., kc_lower=95.),
        dict(timestamp=120_000, is_closed=True, open=100., high=106., low=99., close=105.5 if side == "LONG" else 94.5,
             atr=1., ma3=103., kc_upper=105., kc_lower=95.),
        dict(timestamp=180_000, is_closed=False, open=105., high=107., low=98., close=99. if side == "LONG" else 101.,
             atr=1., ma3=102., kc_upper=105., kc_lower=95.),
    ]
    if side == "SHORT":
        rows[2].update(open=95., high=102., low=93., close=101., ma3=98.)
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    result = evaluate_outer_rail_reversal_exit(_position(side), frame)
    assert result is None
    frame.loc[2, "is_closed"] = True
    result = evaluate_outer_rail_reversal_exit(_position(side), frame)
    assert result and result["side"] == side
    assert result["reason"] == ("EXIT_LONG_AT_PEAK" if side == "LONG" else "EXIT_SHORT_AT_BOTTOM")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_central_entry_gate_allows_confirmed_pivot_without_breakout_filters(monkeypatch, side):
    import core.services.market_consolidation_filter as consolidation
    from core.gates.central_entry_gate import CentralEntryGate

    monkeypatch.setattr(consolidation, "evaluate_market_blacklist", lambda *_a, **_k: (False, "PASS", {}))
    rows = [dict(
        timestamp=60_000 * (i + 1), is_closed=True,
        open=100., high=100.5, low=99.5, close=100., volume=1., atr=0.5,
        kc_upper=101., kc_middle=100., kc_lower=99., ma3=99.9,
        ma5=100.1, ma15=100.2,
    ) for i in range(25)]
    if side == "LONG":
        rows[-3].update(high=100.5, low=99.5)
        rows[-2].update(open=99.8, close=99.6, high=100., low=99., atr=0.5)
        rows[-1].update(open=99.6, close=100.6, high=100.7, low=99.5, atr=0.5,
                        kc_middle=99.9, ma5=99.8, volume=0.1)
        quote = 100.6
    else:
        rows[-3].update(high=100.5, low=99.5)
        rows[-2].update(open=100.2, close=100.4, high=101., low=100., atr=0.5)
        rows[-1].update(open=100.4, close=99.4, high=100.5, low=99.3, atr=0.5,
                        kc_middle=100.1, ma5=100.2, volume=0.1)
        quote = 99.4
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    result = CentralEntryGate.verify_and_authorize(
        "X", side, quote, frame, source=f"UNIFIED_PIPELINE|SWING_PIVOT_{side}",
    )
    assert result.passed, (result.reason, result.checks)
    assert result.checks["market_blacklist"]["reason"] == "PASS_CONFIRMED_SWING_PIVOT"

    blocked = CentralEntryGate.verify_and_authorize(
        "X", side, quote, frame, source=f"UNIFIED_PIPELINE|ENTRY_{side}_MA3_MA15_CROSS_MID",
    )
    assert not blocked.passed

    invalid = frame.copy()
    if side == "LONG":
        invalid.loc[invalid.index[-1], "low"] = 98.9
    else:
        invalid.loc[invalid.index[-1], "high"] = 101.1
    rejected = CentralEntryGate.verify_and_authorize(
        "X", side, quote, invalid, source=f"UNIFIED_PIPELINE|SWING_PIVOT_{side}",
    )
    assert not rejected.passed
    assert rejected.reason == "VETO_SWING_PIVOT_NOT_CONFIRMED"


def test_final_entry_math_revalidates_swing_pivot_before_its_direction_override():
    import time
    from core.services.math_execution_gate import validate_entry_math

    base = int(time.time() // 60 * 60_000) - 3 * 60_000
    rows = []
    for i in range(4):
        rows.append(dict(
            timestamp=base + i * 60_000, is_closed=True,
            open=99. if i == 3 else 99. if i == 2 else 100.,
            high=100. if i >= 2 else 101.,
            low=98. if i == 2 else 98.5 if i == 3 else 99.,
            close=100. if i == 3 else 98.5 if i == 2 else 100.,
            volume=2. if i == 3 else 1., atr=1.,
            kc_middle=100.5 if i < 3 else 100.,
            kc_upper=110.5 if i == 3 else 110., kc_lower=89.5 if i == 3 else 90.,
            ma3=100. if i == 3 else 99., ma5=100. if i == 3 else 99.,
            ma15=99.5,
        ))
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    valid, reason = validate_entry_math(frame, "LONG", is_swing_pivot=True)
    assert valid, reason
    invalid, reason = validate_entry_math(frame, "LONG", is_swing_pivot=False)
    assert not invalid and reason == "BLOCKED_LONG_ON_BEARISH_TREND"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_confirmed_true_peak_or_valley_exits_without_waiting_for_midline(side, monkeypatch):
    from core.gates.unified_pipeline_gates import ExitGate
    base = 1_700_000_000_000

    if side == "LONG":
        rows = [
            dict(timestamp=base, open=100., high=100.4, low=99.5, close=100.,
                 ma5=100., ema13=100., ma15=100., atr=1., kc_upper=110., kc_middle=100., kc_lower=90.),
            dict(timestamp=base + 60_000, open=101., high=102.2, low=100.8, close=102.,
                 ma5=102., ema13=100., ma15=100., atr=1., kc_upper=110., kc_middle=100., kc_lower=90.),
            dict(timestamp=base + 120_000, open=102., high=104., low=101.5, close=103.,
                 ma5=103., ema13=100., ma15=100., atr=1., kc_upper=110., kc_middle=100., kc_lower=90.),
            dict(timestamp=base + 180_000, open=103., high=103.2, low=101.5, close=101.8,
                 ma5=101.8, ema13=100.5, ma15=100.5, atr=1., kc_upper=110., kc_middle=100.5, kc_lower=90.),
        ]
        expected_reason = "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT"
    else:
        rows = [
            dict(timestamp=base, open=100., high=100.5, low=99.5, close=100.,
                 ma5=100., ema13=100., ma15=100., atr=1., kc_upper=110., kc_middle=100., kc_lower=90.),
            dict(timestamp=base + 60_000, open=100., high=100.2, low=98.8, close=99.,
                 ma5=99., ema13=100., ma15=100., atr=1., kc_upper=110., kc_middle=100., kc_lower=90.),
            dict(timestamp=base + 120_000, open=99., high=99.5, low=96., close=98.,
                 ma5=98., ema13=100., ma15=100., atr=1., kc_upper=110., kc_middle=100., kc_lower=90.),
            dict(timestamp=base + 180_000, open=98., high=99.5, low=97.8, close=99.2,
                 ma5=99.2, ema13=99.5, ma15=99.5, atr=1., kc_upper=110., kc_middle=99.5, kc_lower=90.),
        ]
        expected_reason = "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
    frame = pd.DataFrame(rows)
    frame["is_closed"] = True
    frame.attrs["timeframe_ms"] = 60_000
    position = _position(side)
    position["open_timestamp"] = base / 1000.0

    monkeypatch.setattr(
        ExitGate, "_evaluate_with_holding_rules",
        staticmethod(lambda *_args, **_kwargs: (False, "VETO_HOLD", {})),
    )
    monkeypatch.setattr(
        "core.services.exits.abnormal_candle_exit.two_closed_adverse_abnormal_evidence",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "core.services.swing_pivot_rules.evaluate_outer_rail_reversal_exit",
        lambda *_args, **_kwargs: None,
    )
    should_exit, reason, _ = ExitGate.evaluate(
        position, {"raw_df": frame, "current_price": float(frame.iloc[-1].close), "atr": 1.0},
    )

    assert should_exit is True
    assert reason == expected_reason
    from core.services.math_execution_gate import validate_exit_math
    allowed, math_reason, _ = validate_exit_math(
        position, frame, float(frame.iloc[-1].close),
        close_reason=reason, now_ms=base + 180_000,
    )
    assert allowed, math_reason
