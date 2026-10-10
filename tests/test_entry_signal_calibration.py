import time

import pandas as pd
import pytest

from core.services import entry_contract
from core.services.entry_contract import (
    MA_CROSS_FAST_LONG_CODE,
    evaluate_entry_contract,
    evaluate_golden_cross_fast_lane,
    evaluate_three_bar_outer_breakout,
    excessive_upper_shadow_problem,
)


def three_bar_frame(side="LONG"):
    stamp = int(time.time() // 60) * 60_000
    rows = []
    for index in range(9):
        close = 99.9 - index * 0.01
        rows.append(dict(
            timestamp=stamp - (12 - index) * 60_000,
            open=close - 0.05,
            high=close + 0.1,
            low=close - 0.15,
            close=close,
            atr=1.0,
            ma3=99.8 - index * 0.01,
            ma5=99.7 - index * 0.01,
            ma15=100.0 - index * 0.01,
            kc_lower=98.0 - index * 0.01,
            kc_middle=100.0 - index * 0.01,
            kc_upper=102.0 - index * 0.01,
            is_closed=True,
        ))
    rows.extend([
        dict(timestamp=stamp - 180_000, open=100.0, high=100.2,
             low=99.8, close=100.1, atr=1.0, ma3=100.0,
             ma5=99.8, ma15=100.0, kc_lower=99.0,
             kc_middle=100.0, kc_upper=101.0, is_closed=True),
        dict(timestamp=stamp - 120_000, open=100.8, high=101.7,
             low=100.7, close=101.5, atr=1.0, ma3=100.3,
             ma5=100.1, ma15=100.2, kc_lower=99.1,
             kc_middle=100.1, kc_upper=101.1, is_closed=True),
        dict(timestamp=stamp - 60_000, open=101.4, high=101.8,
             low=101.3, close=101.7, atr=1.0, ma3=100.5,
             ma5=100.5, ma15=100.4, kc_lower=99.2,
             kc_middle=100.2, kc_upper=101.3, is_closed=True),
        dict(timestamp=stamp, open=101.6, high=101.9,
             low=101.5, close=101.8, atr=1.0, ma3=100.7,
             ma5=100.8, ma15=100.6, kc_lower=99.3,
             kc_middle=100.3, kc_upper=101.4, is_closed=False),
    ])
    frame = pd.DataFrame(rows)
    if side == "SHORT":
        source = frame.copy()
        for target, original in (
            ("open", "open"), ("close", "close"),
            ("high", "low"), ("low", "high"),
            ("kc_upper", "kc_lower"), ("kc_lower", "kc_upper"),
            ("kc_middle", "kc_middle"), ("ma3", "ma3"),
            ("ma5", "ma5"), ("ma15", "ma15"),
        ):
            frame[target] = 200.0 - source[original]
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def golden_cross_frame():
    frame = three_bar_frame()
    closed = frame.index[-2]
    live = frame.index[-1]
    frame.loc[closed, ["ma5", "ma15"]] = [100.0, 100.2]
    frame.loc[live, [
        "open", "high", "low", "close", "kc_upper", "kc_middle",
        "ma5", "ma15",
    ]] = [102.6, 103.1, 102.5, 102.8, 103.0, 100.8, 102.6, 102.4]
    return frame


def test_live_ma5_ma15_cross_is_a_qualified_fast_lane():
    frame = golden_cross_frame()

    decision = evaluate_golden_cross_fast_lane(frame, 103.1, "CAP/USDT")

    assert decision is not None
    assert decision["type"] == MA_CROSS_FAST_LONG_CODE
    assert decision["entry_phase"] == "MA_CROSS_FAST_LANE"


def test_live_fast_lane_rejects_incomplete_body_with_excessive_upper_wick():
    frame = golden_cross_frame()
    frame.loc[frame.index[-1], "high"] = 104.0

    assert evaluate_golden_cross_fast_lane(
        frame, 103.2, "CAP/USDT",
    ) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_general_entry_requires_first_break_second_confirmation_and_live_third_bar(side):
    frame = three_bar_frame(side)
    quote = float(frame.iloc[-1]["close"])
    decision = evaluate_three_bar_outer_breakout(frame, quote, "CAP/USDT")

    assert decision is not None
    assert decision["side"] == side
    assert decision["type"] == "TRIGGER_A_KC_BREAKOUT"
    assert decision["entry_phase"] == "KC_THREE_BAR_BREAKOUT"


@pytest.mark.parametrize(
    ("bar_offset", "field", "value"),
    [
        (-2, "close", 101.0),
        (-1, "close", 101.4),
    ],
)
def test_general_entry_waits_when_confirmation_or_third_bar_loses_direction(
    bar_offset, field, value,
):
    frame = three_bar_frame()
    frame.loc[frame.index[bar_offset], field] = value

    assert evaluate_three_bar_outer_breakout(
        frame, float(frame.iloc[-1]["close"]), "CAP/USDT",
    ) is None


def test_final_upper_shadow_guard_uses_completed_candle_only():
    frame = three_bar_frame()
    frame.loc[frame.index[-2], ["open", "high", "low", "close"]] = [
        101.4, 102.01, 101.3, 101.7,
    ]
    frame.loc[frame.index[-1], "high"] = 110.0

    assert excessive_upper_shadow_problem(
        frame, 101.8, "LONG", trigger_type="TRIGGER_A_KC_BREAKOUT",
    ) == "BLOCKED_BY_EXCESSIVE_UPPER_SHADOW"


def test_two_closed_bar_breakout_runs_before_continuation_fallback(monkeypatch):
    frame = three_bar_frame()
    monkeypatch.setattr(
        entry_contract, "check_entry_gates", lambda *_args, **_kwargs: (True, "OK"),
    )

    decision = evaluate_entry_contract(
        frame, float(frame.iloc[-1]["close"]), symbol="CAP/USDT",
    )

    assert decision is not None
    assert decision["type"] == "KC_2BAR_CONFIRM_LONG"
    assert decision["entry_phase"] == "KC_2BAR_CLOSED_CONFIRM"
