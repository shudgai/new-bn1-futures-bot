import pandas as pd
import pytest

from core.gates.holding_protection_gate import (
    HoldingProtectionExitGate,
    confirmed_midline_reversal_evidence,
)
from core.gates.pipeline import entry_market_structure_problem
from core.services.strategies.outer_strategy import resolve_kc_direction


def _entry_frame(side, quote, *, opening=None, ma5=None, ma15=None):
    middle, upper, lower = 100.0, 110.0, 90.0
    if side == "SHORT":
        middle, upper, lower = 100.0, 110.0, 90.0
        opening = 100.5 if opening is None else opening
        ma5 = 99.0 if ma5 is None else ma5
        ma15 = 100.0 if ma15 is None else ma15
    else:
        opening = 99.5 if opening is None else opening
        ma5 = 101.0 if ma5 is None else ma5
        ma15 = 100.0 if ma15 is None else ma15
    rows = [
        {
            "timestamp": (index + 1) * 60_000,
            "is_closed": index < 9,
            "open": opening,
            "high": max(opening, quote),
            "low": min(opening, quote),
            "close": quote,
            "kc_upper": upper,
            "kc_middle": middle,
            "kc_lower": lower,
            "ma5": ma5,
            "ma15": ma15,
            "atr": 1.0,
        }
        for index in range(10)
    ]
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame




@pytest.mark.parametrize(
    "side,quote,opening,ma5,ma15,expected",
    [
        ("LONG", 99.0, 99.5, 99.0, 100.0, "BEAR_TREND_NO_LONG"),
        ("SHORT", 101.0, 100.5, 101.0, 100.0, "BULL_TREND_NO_SHORT"),
        ("LONG", 102.0, 99.5, 101.0, 100.0, None),
        ("SHORT", 98.0, 100.5, 99.0, 100.0, None),
    ],
)
def test_entries_block_countertrend_and_require_current_midline_cross(
    side, quote, opening, ma5, ma15, expected,
):
    frame = _entry_frame(
        side, quote, opening=opening, ma5=ma5, ma15=ma15,
    )

    assert entry_market_structure_problem(frame, quote, side) == expected


@pytest.mark.parametrize(
    "side,quote,opening",
    [
        ("LONG", 102.0, 99.5),
        ("SHORT", 98.0, 100.5),
    ],
)
def test_closed_midline_cross_cannot_authorize_late_entry(side, quote, opening):
    frame = _entry_frame(side, quote, opening=opening)
    frame.loc[frame.index[-1], "is_closed"] = True

    assert entry_market_structure_problem(
        frame, quote, side,
    ) == "KC_MIDLINE_ENTRY_REQUIRES_LIVE_CANDLE"


@pytest.mark.parametrize(
    "slope,price,middle,ma5,ma15,expected",
    [
        (-0.01, 100.0, 100.0, 99.0, 100.0, "DOWN"),
        (0.01, 100.0, 100.0, 101.0, 100.0, "UP"),
    ],
)
def test_channel_direction_uses_slope_and_ma_alignment_without_atr_delay(
    slope, price, middle, ma5, ma15, expected,
):
    assert resolve_kc_direction(
        slope, price, middle, ma5, ma15, atr=10.0,
    ) == expected


def _closed_exit_frame(side, close, middle, ma15):
    rows = []
    for index in range(3):
        rows.append({
            "timestamp": (index + 1) * 60_000,
            "is_closed": True,
            "open": close + 0.1 if side == "LONG" else close - 0.1,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "kc_middle": middle,
            "kc_upper": middle + 3.0,
            "kc_lower": middle - 3.0,
            "ma15": ma15,
            "atr": 1.0,
            "ma5": close,
        })
    return pd.DataFrame(rows)


@pytest.mark.parametrize(
    "side,close,middle,ma15,should_exit",
    [
        ("LONG", 99.0, 100.0, 98.0, True),
        ("LONG", 100.1, 100.0, 101.0, False),
        ("SHORT", 101.0, 100.0, 102.0, True),
        ("SHORT", 99.9, 100.0, 99.0, False),
    ],
)
def test_soft_exit_evidence_uses_only_closed_kc_middle_not_ma15(
    side, close, middle, ma15, should_exit,
):
    frame = _closed_exit_frame(side, close, middle, ma15)

    evidence = confirmed_midline_reversal_evidence(frame, side)

    assert (evidence is not None) is should_exit


@pytest.mark.parametrize(
    "side,opening,close,middle",
    [
        ("LONG", 98.0, 99.0, 100.0),
        ("SHORT", 102.0, 101.0, 100.0),
    ],
)
def test_midline_close_without_adverse_body_does_not_authorize_exit(
    side, opening, close, middle,
):
    frame = _closed_exit_frame(side, close, middle, ma15=middle)
    frame.loc[frame.index[-1], "open"] = opening

    assert confirmed_midline_reversal_evidence(frame, side) is None


@pytest.mark.parametrize(
    "side,reason,close,middle",
    [
        ("LONG", "EXIT_BY_VERIFIED_FRACTAL_PEAK", 100.1, 100.0),
        ("SHORT", "EXIT_BY_VERIFIED_FRACTAL_VALLEY", 99.9, 100.0),
    ],
)
def test_ma_or_pivot_exit_is_rejected_while_closed_price_holds_midline(
    side, reason, close, middle,
):
    frame = _closed_exit_frame(side, close, middle, ma15=middle)
    position = {"side": side, "entry_price": 100.0, "open_timestamp": 1.0}

    allowed, _, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, close, reason,
    )

    assert not allowed
