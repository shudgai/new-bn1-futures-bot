from types import SimpleNamespace

import pandas as pd

from core.services.strategies.three_mode_entry_engine import (
    MODE_A_LONG,
    MODE_A_SHORT,
    MODE_B_LONG,
    MODE_B_SHORT,
    MODE_C_SHORT,
    evaluate_entry_signal,
)


def _frame(count=20):
    timestamp = 1_700_000_000_000
    rows = []
    for index in range(count):
        rows.append({
            "timestamp": timestamp + index * 60_000,
            "is_closed": True,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100.0,
            "kc_upper": 110.0,
            "kc_middle": 100.0,
            "kc_lower": 90.0,
            "ma5": 99.0,
            "ma15": 100.0,
            "atr": 2.0,
        })
    return pd.DataFrame(rows).assign(is_closed=True).pipe(
        lambda data: _add_frame_attrs(data)
    )


def _add_frame_attrs(frame):
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def _set_last(frame, **values):
    for key, value in values.items():
        frame.loc[frame.index[-1], key] = value
    return frame


def test_mode_a_long_requires_ma_cross_rising_middle_and_valid_location():
    frame = _frame()
    frame.loc[frame.index[-2], ["close", "kc_middle", "ma5"]] = [100.0, 99.5, 99.0]
    _set_last(
        frame,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        kc_middle=100.0,
        ma5=101.0,
        ma15=100.0,
    )

    decision = evaluate_entry_signal(frame, quote=101.0, requested_side="LONG")

    assert decision is not None
    assert decision["type"] == MODE_A_LONG
    assert decision["side"] == "LONG"


def test_mode_a_short_requires_ma_cross_falling_middle_and_valid_location():
    frame = _frame()
    frame.loc[frame.index[-2], ["close", "kc_middle", "ma5"]] = [100.0, 100.5, 101.0]
    _set_last(
        frame,
        open=100.0,
        high=101.0,
        low=98.0,
        close=99.0,
        kc_middle=100.0,
        ma5=99.0,
        ma15=100.0,
    )

    decision = evaluate_entry_signal(frame, quote=99.0, requested_side="SHORT")

    assert decision is not None
    assert decision["type"] == MODE_A_SHORT
    assert decision["side"] == "SHORT"


def test_mode_b_volume_breakouts_bypass_only_the_same_side_outer_chase_gate():
    short_frame = _frame()
    _set_last(
        short_frame,
        open=91.0,
        high=91.0,
        low=87.0,
        close=88.0,
        kc_lower=89.0,
        volume=150.0,
    )

    short_decision = evaluate_entry_signal(
        short_frame, quote=88.0, requested_side="SHORT",
    )

    long_frame = _frame()
    _set_last(
        long_frame,
        open=109.0,
        high=113.0,
        low=109.0,
        close=112.0,
        kc_upper=111.0,
        volume=150.0,
    )

    long_decision = evaluate_entry_signal(
        long_frame, quote=112.0, requested_side="LONG",
    )

    assert short_decision is not None
    assert short_decision["type"] == MODE_B_SHORT
    assert long_decision is not None
    assert long_decision["type"] == MODE_B_LONG


def test_mode_a_and_b_are_blocked_by_two_midline_crosses():
    frame = _frame()
    for index in range(10):
        frame.loc[index + 10, "close"] = 101.0 if index % 2 == 0 else 99.0
    _set_last(
        frame,
        open=100.0,
        high=101.0,
        low=99.0,
        close=99.0,
        kc_middle=100.0,
        ma5=98.0,
        ma15=100.0,
        volume=150.0,
    )
    diagnostics = {}

    decision = evaluate_entry_signal(
        frame, quote=99.0, requested_side="SHORT", diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"].startswith("BLOCKED_THREE_MODE_CHOP_CROSSES")


def test_five_completed_bar_cooldown_blocks_new_mode_signals():
    frame = _frame()
    account = SimpleNamespace(
        last_closed_at={
            "LOBSTER/USDT": (float(frame.iloc[-1]["timestamp"]) + 30_000) / 1000.0,
        },
        positions={},
    )
    diagnostics = {}

    decision = evaluate_entry_signal(
        frame,
        account=account,
        symbol="LOBSTER/USDT",
        quote=100.0,
        requested_side="LONG",
        diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"].startswith("BLOCKED_THREE_MODE_COOLDOWN")


def test_mode_c_requires_long_position_and_large_bearish_break_of_middle():
    frame = _frame(count=4)
    frame.loc[frame.index[-4:-1], ["open", "close"]] = [
        [99.5, 100.0],
        [99.5, 100.0],
        [99.5, 100.0],
    ]
    _set_last(
        frame,
        open=103.0,
        high=103.0,
        low=98.0,
        close=98.0,
        ma5=100.0,
    )

    decision = evaluate_entry_signal(
        frame, position={"side": "LONG"}, quote=98.0,
    )
    flat_decision = evaluate_entry_signal(frame, quote=98.0, requested_side="SHORT")

    assert decision is not None
    assert decision["type"] == MODE_C_SHORT
    assert decision["side"] == "SHORT"
    assert flat_decision is None


def test_dump_reversal_bypasses_cooldown_but_not_market_data_validation():
    frame = _frame(count=4)
    frame.loc[frame.index[-4:-1], ["open", "close"]] = [
        [99.5, 100.0],
        [99.5, 100.0],
        [99.5, 100.0],
    ]
    _set_last(
        frame,
        open=103.0,
        high=103.0,
        low=98.0,
        close=98.0,
        ma5=100.0,
    )
    account = SimpleNamespace(
        positions={},
        last_closed_at={"LOBSTER/USDT": 1_700_000_000_000 / 1000.0},
    )

    decision = evaluate_entry_signal(
        frame,
        position={"side": "LONG"},
        account=account,
        symbol="LOBSTER/USDT",
        quote=98.0,
    )

    assert decision is not None
    assert decision["type"] == MODE_C_SHORT

    _set_last(frame, high=97.0)
    assert evaluate_entry_signal(
        frame, position={"side": "LONG"}, quote=98.0,
    ) is None
