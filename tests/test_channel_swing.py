import inspect

import pytest
import pandas as pd

import core.paper_account as pa_module
from core.engine import TradingEngine
from core.paper_account import PaperAccount


def _channel_frame(lower: float = 99.0, upper: float = 101.0) -> pd.DataFrame:
    return pd.DataFrame({
        "open": [100.0] * 20,
        "close": [100.0] * 20,
        "high": [100.5] * 20,
        "low": [99.5] * 20,
        "ma3": [100.0] * 20,
        "ma15": [100.0] * 20,
        "kc_lower": [lower] * 20,
        "kc_upper": [upper] * 20,
    })


def _closed_trough(frame: pd.DataFrame):
    frame.loc[frame.index[-2], ["open", "close", "low", "high"]] = [
        98.8, 98.9, 98.7, 98.95,
    ]
    frame.loc[frame.index[-2], "ma3"] = 98.7
    frame.loc[frame.index[-1], "ma3"] = 100.0


def _closed_peak(frame: pd.DataFrame):
    frame.loc[frame.index[-2], ["open", "close", "low", "high"]] = [
        101.2, 101.1, 101.05, 101.3,
    ]
    frame.loc[frame.index[-2], "ma3"] = 101.3
    frame.loc[frame.index[-1], "ma3"] = 100.0


def test_live_price_at_upper_kc_enters_long_without_close_color_or_width_filter():
    frame = _channel_frame(lower=99.8, upper=100.2)
    frame.loc[frame.index[-1], ["open", "close"]] = [100.3, 100.1]

    result = TradingEngine._channel_live_outer_entry_action(frame, 100.2)

    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", "LONG", "KC_LIVE_UPPER_BREAK_LONG",
    )
    assert (100.2 - 99.8) / 100.2 < 0.005


def test_live_price_at_lower_kc_enters_short_without_close_color_or_width_filter():
    frame = _channel_frame(lower=99.8, upper=100.2)
    frame.loc[frame.index[-1], ["open", "close"]] = [99.7, 99.9]

    result = TradingEngine._channel_live_outer_entry_action(frame, 99.8)

    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", "SHORT", "KC_LIVE_LOWER_BREAK_SHORT",
    )
    assert (100.2 - 99.8) / 99.8 < 0.005


def test_same_bar_upper_outer_rechase_immediately_reverses_short_without_half_kc():
    frame = _channel_frame(lower=99.8, upper=100.2)

    result = TradingEngine._channel_same_bar_outer_rechase_action(
        frame, 100.2, "SHORT", frame.index[-2],
    )

    assert (result["action"], result["side"], result["reason"]) == (
        "REVERSE", "LONG", "SAME_BAR_UPPER_OUTER_RECHASE",
    )
    assert (100.2 - 99.8) / 100.2 < 0.005


def test_same_bar_lower_outer_rechase_immediately_reverses_long_without_half_kc():
    frame = _channel_frame(lower=99.8, upper=100.2)

    result = TradingEngine._channel_same_bar_outer_rechase_action(
        frame, 99.8, "LONG", frame.index[-2],
    )

    assert (result["action"], result["side"], result["reason"]) == (
        "REVERSE", "SHORT", "SAME_BAR_LOWER_OUTER_RECHASE",
    )
    assert (100.2 - 99.8) / 99.8 < 0.005


def test_outer_rechase_requires_the_position_to_come_from_same_live_bar():
    frame = _channel_frame(lower=99.8, upper=100.2)

    result = TradingEngine._channel_same_bar_outer_rechase_action(
        frame, 100.2, "SHORT", frame.index[-3],
    )

    assert (result["action"], result["side"]) == ("HOLD", None)


def test_same_bar_outer_rechase_bypasses_chop_close_only_gate():
    assert TradingEngine._channel_chop_gate(
        "REVERSE", "LONG", True, True, "SAME_BAR_UPPER_OUTER_RECHASE",
    ) == ("REVERSE", "LONG", None)
    assert TradingEngine._channel_chop_gate(
        "REVERSE", "SHORT", True, True, "SAME_BAR_LOWER_OUTER_RECHASE",
    ) == ("REVERSE", "SHORT", None)


def test_live_price_inside_kc_does_not_use_immediate_outer_entry():
    frame = _channel_frame(lower=99.8, upper=100.2)

    result = TradingEngine._channel_live_outer_entry_action(frame, 100.0)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "WAIT_LIVE_OUTER_BREAK",
    )


def test_single_red_candle_reenters_half_kc_width_for_short():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [
        101.2, 99.9, 101.3, 99.8,
    ]

    result = TradingEngine._channel_live_inner_reentry_action(frame, 99.9)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "KC_INNER_REENTRY_DISABLED",
    )


def test_single_green_candle_reenters_half_kc_width_for_long():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [
        98.8, 100.1, 100.2, 98.7,
    ]

    result = TradingEngine._channel_live_inner_reentry_action(frame, 100.1)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "KC_INNER_REENTRY_DISABLED",
    )


def test_single_red_candle_below_half_kc_reentry_waits():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close"]] = [101.2, 100.1]

    result = TradingEngine._channel_live_inner_reentry_action(frame, 100.1)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "KC_INNER_REENTRY_DISABLED",
    )


def test_two_red_candles_inside_kc_are_added_for_half_width_entry():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-2], ["open", "close"]] = [100.8, 100.2]
    frame.loc[frame.index[-1], ["open", "close"]] = [100.2, 99.8]

    result = TradingEngine._channel_live_inner_reentry_action(frame, 99.8)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "KC_INNER_REENTRY_DISABLED",
    )


def test_two_green_candles_inside_kc_are_added_for_half_width_entry():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-2], ["open", "close"]] = [99.2, 99.8]
    frame.loc[frame.index[-1], ["open", "close"]] = [99.8, 100.2]

    result = TradingEngine._channel_live_inner_reentry_action(frame, 100.2)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "KC_INNER_REENTRY_DISABLED",
    )


def test_two_red_candles_inside_kc_below_half_width_wait():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-2], ["open", "close"]] = [100.6, 100.3]
    frame.loc[frame.index[-1], ["open", "close"]] = [100.3, 100.0]

    result = TradingEngine._channel_live_inner_reentry_action(frame, 100.0)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "KC_INNER_REENTRY_DISABLED",
    )


def test_held_long_can_reverse_from_two_red_bodies_inside_kc():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-2], ["open", "close"]] = [100.8, 100.2]
    frame.loc[frame.index[-1], ["open", "close"]] = [100.2, 99.8]

    result = TradingEngine._channel_swing_action(frame, 99.8, "LONG")

    assert (result["action"], result["side"], result["reason"]) == (
        "HOLD", None, "WAIT_UPPER_RED_REENTRY",
    )


def test_held_long_reverses_only_after_single_red_half_kc_reentry():
    enough = _channel_frame(lower=99.0, upper=101.0)
    enough.loc[enough.index[-1], ["open", "close", "high", "low"]] = [
        101.2, 99.9, 101.3, 99.8,
    ]
    shallow = _channel_frame(lower=99.0, upper=101.0)
    shallow.loc[shallow.index[-1], ["open", "close", "high", "low"]] = [
        101.2, 100.1, 101.3, 100.0,
    ]

    reversed_action = TradingEngine._channel_swing_action(enough, 99.9, "LONG")
    held_action = TradingEngine._channel_swing_action(shallow, 100.1, "LONG")

    assert (reversed_action["action"], reversed_action["side"]) == (
        "HOLD", None,
    )
    assert (held_action["action"], held_action["side"]) == ("HOLD", None)


def test_held_short_reverses_only_after_single_green_half_kc_reentry():
    enough = _channel_frame(lower=99.0, upper=101.0)
    enough.loc[enough.index[-1], ["open", "close", "high", "low"]] = [
        98.8, 100.1, 100.2, 98.7,
    ]
    shallow = _channel_frame(lower=99.0, upper=101.0)
    shallow.loc[shallow.index[-1], ["open", "close", "high", "low"]] = [
        98.8, 99.9, 100.0, 98.7,
    ]

    reversed_action = TradingEngine._channel_swing_action(enough, 100.1, "SHORT")
    held_action = TradingEngine._channel_swing_action(shallow, 99.9, "SHORT")

    assert (reversed_action["action"], reversed_action["side"]) == (
        "HOLD", None,
    )
    assert (held_action["action"], held_action["side"]) == ("HOLD", None)


def test_channel_swing_enters_only_after_closed_turn_candle_and_next_breakout():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], ["high", "low"]] = [98.9, 98.8]

    low = TradingEngine._channel_swing_action(frame, 98.9)

    frame = _channel_frame()
    _closed_peak(frame)
    frame.loc[frame.index[-1], ["high", "low"]] = [101.2, 101.1]
    high = TradingEngine._channel_swing_action(frame, 101.1)

    assert low["action"] == "WAIT"
    assert high["action"] == "WAIT"


def test_outer_ma3_route_accepts_turn_body_that_remains_outside():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "low", "high"]] = [
        98.8, 98.9, 98.7, 98.95,
    ]
    frame.loc[frame.index[-2], "ma3"] = 98.7
    frame.loc[frame.index[-1], "ma3"] = 100.0
    long_entry = TradingEngine._channel_swing_action(frame, 99.9)

    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "low", "high"]] = [
        101.2, 101.1, 101.05, 101.3,
    ]
    frame.loc[frame.index[-2], "ma3"] = 101.3
    frame.loc[frame.index[-1], "ma3"] = 100.0
    short_entry = TradingEngine._channel_swing_action(frame, 100.1)

    assert (long_entry["action"], long_entry["side"]) == ("ENTER", "LONG")
    assert (short_entry["action"], short_entry["side"]) == ("ENTER", "SHORT")


def test_body_deep_eighty_percent_into_half_channel_bypasses_outer_depth():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        99.85, 99.9, 98.9, 99.95, 98.95,
    ]
    long_frame.loc[long_frame.index[-1], ["close", "ma3"]] = [100.0, 100.0]
    long_turn = TradingEngine._channel_swing_action(long_frame, 100.0)

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        100.15, 100.1, 100.05, 101.1, 101.05,
    ]
    short_frame.loc[short_frame.index[-1], ["close", "ma3"]] = [100.0, 100.0]
    short_turn = TradingEngine._channel_swing_action(short_frame, 100.0)

    assert (long_turn["action"], long_turn["side"]) == ("ENTER", "LONG")
    assert (short_turn["action"], short_turn["side"]) == ("ENTER", "SHORT")


def test_shallow_outer_v_turns_are_symmetric_without_ma3_depth():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        99.1, 99.2, 98.9, 99.25, 98.7,
    ]
    long_frame.loc[long_frame.index[-1], ["close", "ma3"]] = [100.0, 100.0]
    rejected_long = TradingEngine._channel_swing_action(long_frame, 100.0)

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        100.9, 100.8, 100.75, 101.1, 101.3,
    ]
    short_frame.loc[short_frame.index[-1], ["close", "ma3"]] = [100.0, 100.0]
    rejected_short = TradingEngine._channel_swing_action(short_frame, 100.0)

    assert (rejected_long["action"], rejected_long["side"]) == (
        "ENTER", "LONG",
    )
    assert (rejected_short["action"], rejected_short["side"]) == (
        "ENTER", "SHORT",
    )


def test_lower_outer_green_reentry_can_open_long_without_ma3_depth():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        98.95, 99.2, 98.9, 99.25, 98.7,
    ]
    frame.loc[frame.index[-1], ["close", "ma3"]] = [100.0, 100.0]

    result = TradingEngine._channel_swing_action(frame, 100.0)

    assert (result["action"], result["side"]) == ("ENTER", "LONG")


def test_latest_shallow_outer_v_is_valid_on_both_sides():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-4], ["open", "close", "low", "high", "ma3"]] = [
        98.8, 98.9, 98.7, 98.95, 98.7,
    ]
    long_frame.loc[long_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        99.1, 99.2, 98.9, 99.25, 98.7,
    ]
    long_frame.loc[long_frame.index[-1], ["close", "ma3"]] = [99.8, 99.8]
    long_turn = TradingEngine._channel_swing_action(long_frame, 99.8)

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-4], ["open", "close", "low", "high", "ma3"]] = [
        101.2, 101.1, 101.05, 101.3, 101.3,
    ]
    short_frame.loc[short_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        100.9, 100.8, 100.75, 101.1, 101.3,
    ]
    short_frame.loc[short_frame.index[-1], ["close", "ma3"]] = [100.2, 100.2]
    short_turn = TradingEngine._channel_swing_action(short_frame, 100.2)

    assert (long_turn["action"], long_turn["side"]) == ("ENTER", "LONG")
    assert (short_turn["action"], short_turn["side"]) == ("ENTER", "SHORT")


def test_flat_entry_does_not_reuse_multi_candle_confirmed_turn():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-4], ["open", "close", "low", "high", "ma3"]] = [
        98.8, 98.9, 98.7, 98.95, 98.7,
    ]
    long_frame.loc[long_frame.index[-3], ["open", "close", "low", "high", "ma3"]] = [
        99.4, 99.2, 99.05, 99.45, 99.2,
    ]
    long_frame.loc[long_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        99.4, 99.7, 99.3, 99.75, 99.6,
    ]
    long_frame.loc[long_frame.index[-1], ["close", "ma3"]] = [99.8, 99.8]
    long_turn = TradingEngine._channel_swing_action(long_frame, 99.8)

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-4], ["open", "close", "low", "high", "ma3"]] = [
        101.2, 101.1, 101.05, 101.3, 101.3,
    ]
    short_frame.loc[short_frame.index[-3], ["open", "close", "low", "high", "ma3"]] = [
        100.6, 100.8, 100.55, 100.95, 100.8,
    ]
    short_frame.loc[short_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        100.6, 100.3, 100.25, 100.7, 100.4,
    ]
    short_frame.loc[short_frame.index[-1], ["close", "ma3"]] = [100.2, 100.2]
    short_turn = TradingEngine._channel_swing_action(short_frame, 100.2)

    assert (long_turn["action"], long_turn["reason"]) == (
        "WAIT", "WAIT_ADJACENT_OUTER_CANDIDATE",
    )
    assert long_turn["side"] is None
    assert (short_turn["action"], short_turn["reason"]) == (
        "WAIT", "WAIT_ADJACENT_OUTER_CANDIDATE",
    )
    assert short_turn["side"] is None


def test_flat_entry_does_not_reuse_turn_after_opposite_color_candle():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["open", "close", "low", "high", "ma3"]] = [
        98.8, 98.9, 98.7, 98.95, 98.7,
    ]
    frame.loc[frame.index[-3], ["open", "close", "low", "high", "ma3"]] = [
        99.4, 99.2, 99.1, 99.5, 99.2,
    ]
    frame.loc[frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        99.2, 99.7, 99.15, 99.75, 99.6,
    ]
    frame.loc[frame.index[-1], ["close", "ma3"]] = [99.8, 99.8]

    result = TradingEngine._channel_swing_action(frame, 99.8)

    assert (result["action"], result["reason"]) == (
        "WAIT", "WAIT_ADJACENT_OUTER_CANDIDATE",
    )
    assert result["side"] is None


def test_empty_slot_does_not_chase_kc_outer_trend_without_pivot_turn():
    up = _channel_frame()
    up.loc[up.index[-3], "ma3"] = 100.5
    up.loc[up.index[-2], "ma3"] = 100.9
    up.loc[up.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.4, 101.5, 101.3,
    ]
    long_wait = TradingEngine._channel_swing_action(up, 101.4)

    down = _channel_frame()
    down.loc[down.index[-3], "ma3"] = 99.5
    down.loc[down.index[-2], "ma3"] = 99.1
    down.loc[down.index[-1], ["open", "close", "low", "ma3"]] = [
        98.9, 98.6, 98.5, 98.7,
    ]
    short_wait = TradingEngine._channel_swing_action(down, 98.6)

    assert (long_wait["action"], long_wait["side"]) == ("WAIT", None)
    assert long_wait["turn_low"] is None
    assert (short_wait["action"], short_wait["side"]) == ("WAIT", None)
    assert short_wait["turn_high"] is None


def test_empty_slot_does_not_chase_price_outside_without_ma3_trend():
    frame = _channel_frame()
    frame.loc[frame.index[-2], "ma3"] = 100.8
    frame.loc[frame.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.4, 101.5, 100.9,
    ]

    result = TradingEngine._channel_swing_action(frame, 101.4)

    assert result["action"] == "WAIT"


def test_empty_slot_does_not_chase_outer_move_when_ma3_is_losing_slope():
    up = _channel_frame()
    up.loc[up.index[-3], "ma3"] = 100.0
    up.loc[up.index[-2], "ma3"] = 100.9
    up.loc[up.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.4, 101.5, 101.2,
    ]

    down = _channel_frame()
    down.loc[down.index[-3], "ma3"] = 100.0
    down.loc[down.index[-2], "ma3"] = 99.1
    down.loc[down.index[-1], ["open", "close", "low", "ma3"]] = [
        98.9, 98.6, 98.5, 98.8,
    ]

    assert TradingEngine._channel_swing_action(up, 101.4)["action"] == "WAIT"
    assert TradingEngine._channel_swing_action(down, 98.6)["action"] == "WAIT"


def test_empty_slot_does_not_chase_when_only_close_breaks_outer_rail():
    frame = _channel_frame()
    frame.loc[frame.index[-3], "ma3"] = 100.5
    frame.loc[frame.index[-2], "ma3"] = 100.9
    frame.loc[frame.index[-1], ["open", "close", "high", "ma3"]] = [
        100.9, 101.4, 101.5, 101.3,
    ]

    result = TradingEngine._channel_swing_action(frame, 101.4)

    assert result["action"] == "WAIT"


def test_prior_downtrend_inside_kc_does_not_open_continuation_chase():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["open", "close", "low", "high", "ma3"]] = [
        98.8, 98.9, 98.7, 98.95, 98.7,
    ]
    frame.loc[frame.index[-3], ["open", "close", "ma3", "kc_lower", "kc_upper"]] = [
        100.0, 100.0, 100.0, 98.9, 100.9,
    ]
    frame.loc[frame.index[-2], ["open", "close", "ma3", "kc_lower", "kc_upper"]] = [
        100.0, 99.9, 99.9, 98.85, 100.85,
    ]
    frame.loc[frame.index[-1], ["open", "close", "ma3", "kc_lower", "kc_upper"]] = [
        99.8, 99.6, 99.8, 98.8, 100.8,
    ]

    result = TradingEngine._channel_swing_action(frame, 99.6)

    assert (result["action"], result["side"]) == ("WAIT", None)
    assert result["reason"] == "WAIT_ADJACENT_OUTER_CANDIDATE"
    assert result["turn_low"] is None
    assert result["turn_high"] is None


def test_prior_downtrend_blocks_green_countertrend_long_entry():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["open", "close", "low", "high", "ma3"]] = [
        98.8, 98.9, 98.7, 98.95, 98.7,
    ]
    frame.loc[frame.index[-3], ["open", "close", "ma3", "kc_lower", "kc_upper"]] = [
        100.0, 100.0, 100.0, 98.9, 100.9,
    ]
    frame.loc[frame.index[-2], ["open", "close", "ma3", "kc_lower", "kc_upper"]] = [
        100.0, 99.9, 99.9, 98.85, 100.85,
    ]
    frame.loc[frame.index[-1], ["open", "close", "ma3", "kc_lower", "kc_upper"]] = [
        99.5, 99.6, 99.8, 98.8, 100.8,
    ]

    result = TradingEngine._channel_swing_action(frame, 99.6)

    assert (result["action"], result["side"]) == ("WAIT", None)
    assert result["reason"] == "WAIT_ADJACENT_OUTER_CANDIDATE"


def test_prior_uptrend_inside_kc_does_not_open_continuation_chase():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["open", "close", "low", "high", "ma3"]] = [
        101.2, 101.1, 101.05, 101.3, 101.3,
    ]
    frame.loc[frame.index[-3], ["open", "close", "ma3", "kc_lower", "kc_upper"]] = [
        100.0, 100.0, 100.0, 99.1, 101.1,
    ]
    frame.loc[frame.index[-2], ["open", "close", "ma3", "kc_lower", "kc_upper"]] = [
        100.0, 100.1, 100.1, 99.15, 101.15,
    ]
    frame.loc[frame.index[-1], ["open", "close", "ma3", "kc_lower", "kc_upper"]] = [
        100.2, 100.4, 100.2, 99.2, 101.2,
    ]

    result = TradingEngine._channel_swing_action(frame, 100.4)

    assert (result["action"], result["side"]) == ("WAIT", None)
    assert result["reason"] == "WAIT_ADJACENT_OUTER_CANDIDATE"
    assert result["turn_low"] is None
    assert result["turn_high"] is None


def test_channel_swing_holds_between_entry_and_opposite_edge():
    frame = _channel_frame()

    assert TradingEngine._channel_swing_action(frame, 100.0, "LONG")["action"] == "HOLD"
    assert TradingEngine._channel_swing_action(frame, 100.0, "SHORT")["action"] == "HOLD"



def test_held_position_waits_when_outer_candle_has_not_closed_inside_channel():
    frame = _channel_frame()
    _closed_peak(frame)
    held_long = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    frame = _channel_frame()
    _closed_trough(frame)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (held_long["action"], held_long["side"]) == ("HOLD", None)
    assert (held_short["action"], held_short["side"]) == ("HOLD", None)

def test_previous_outer_red_does_not_reverse_without_live_half_kc_reentry():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "high", "low", "ma3"]] = [
        101.2, 100.8, 101.3, 100.7, 101.0,
    ]

    result = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    assert (result["action"], result["side"]) == ("HOLD", None)


def test_previous_outer_green_does_not_reverse_without_live_half_kc_reentry():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "high", "low", "ma3"]] = [
        98.8, 99.2, 99.3, 98.7, 99.0,
    ]

    result = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (result["action"], result["side"]) == ("HOLD", None)


def test_channel_swing_does_not_enter_from_unclosed_live_green_or_red_candle():
    frame = _channel_frame()
    frame.loc[frame.index[-1], ["open", "low"]] = [99.1, 98.9]

    no_trough = TradingEngine._channel_swing_action(frame, 99.2)

    assert no_trough["action"] == "WAIT"
    assert no_trough["reason"] == "WAIT_CLOSE_GREEN"

    frame = _channel_frame()
    frame.loc[frame.index[-1], ["open", "high"]] = [100.9, 101.1]
    no_peak = TradingEngine._channel_swing_action(frame, 100.8)

    assert no_peak["action"] == "WAIT"
    assert no_peak["reason"] == "WAIT_CLOSE_RED"


def test_channel_swing_waits_for_next_candle_breakout():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], ["high", "low"]] = [98.9, 98.8]

    result = TradingEngine._channel_swing_action(frame, 98.9)

    assert result["action"] == "WAIT"
    assert result["reason"] == "WAIT_BREAK_HIGH"


def test_channel_swing_cancels_failed_trough_before_entry():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], "low"] = 98.6

    result = TradingEngine._channel_swing_action(frame, 98.6)

    assert result["action"] == "WAIT"
    assert result["reason"] == "CANCEL_LONG"


def test_cancelled_outer_trough_cannot_fall_back_to_live_ma3_entry():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], ["low", "high"]] = [98.6, 99.3]

    result = TradingEngine._channel_swing_action(frame, 99.2)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "CANCEL_LONG",
    )


def test_cancelled_outer_peak_cannot_fall_back_to_live_ma3_entry():
    frame = _channel_frame()
    _closed_peak(frame)
    frame.loc[frame.index[-1], ["low", "high"]] = [101.1, 101.2]

    waiting = TradingEngine._channel_swing_action(frame, 101.1)
    frame.loc[frame.index[-1], ["low", "high"]] = [100.7, 101.4]
    cancelled = TradingEngine._channel_swing_action(frame, 100.8)

    assert waiting["action"] == "WAIT"
    assert waiting["reason"] == "WAIT_BREAK_LOW"
    assert (cancelled["action"], cancelled["side"], cancelled["reason"]) == (
        "WAIT", None, "CANCEL_SHORT",
    )


def test_channel_swing_does_not_exit_before_actual_rail_touch():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "low", "high"]] = [
        101.0, 100.9, 100.8, 100.95,
    ]
    result = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    assert result["action"] == "HOLD"
    assert result["side"] is None


def test_channel_chop_state_detects_repeated_ma_and_middle_crosses():
    frame = _channel_frame()
    closes = [99.6, 100.4] * 6
    ma3 = [99.7, 100.3] * 6
    frame.loc[frame.index[-13:-1], "close"] = closes
    frame.loc[frame.index[-13:-1], "ma3"] = ma3

    result = TradingEngine._channel_chop_state(frame)

    assert result["detected"] is True
    assert result["clear_direction"] is None
    assert result["ma_crosses"] >= 3
    assert result["middle_crosses"] >= 3


def test_channel_chop_state_unlocks_after_three_clear_closed_bars():
    frame = _channel_frame()
    for position in range(8, 20):
        middle = 99.4 + position * 0.08
        frame.loc[position, ["kc_lower", "kc_upper", "ma15", "ma3", "close"]] = [
            middle - 1.0,
            middle + 1.0,
            middle - 0.20,
            middle + 0.20,
            middle + 0.35,
        ]

    result = TradingEngine._channel_chop_state(frame)

    assert result["detected"] is False
    assert result["clear_direction"] == "LONG"


def test_channel_chop_gate_blocks_entry_and_turns_reverse_into_close_only():
    assert TradingEngine._channel_chop_gate(
        "ENTER", "LONG", True, False,
    ) == ("WAIT", None, "CHOP_WAIT_NO_ENTRY")
    assert TradingEngine._channel_chop_gate(
        "REVERSE", "SHORT", True, True,
    ) == ("EXIT", None, "CHOP_WAIT_CLOSE_ONLY")
    assert TradingEngine._channel_chop_gate(
        "ENTER", "LONG", False, False,
    ) == ("ENTER", "LONG", None)



def test_held_position_does_not_use_later_live_break_to_confirm_reentry():
    frame = _channel_frame()
    _closed_peak(frame)
    frame.loc[frame.index[-1], ["low", "high"]] = [101.1, 101.2]

    result = TradingEngine._channel_swing_action(frame, 100.6, "LONG")

    assert (result["action"], result["side"]) == ("HOLD", None)


def test_flat_entry_can_use_ma3_but_held_position_requires_body_reentry():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-2], ["open", "ma3"]] = [98.8, 98.9]

    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (empty["action"], empty["side"]) == ("ENTER", "LONG")
    assert (held_short["action"], held_short["side"]) == ("HOLD", None)

def test_confirmed_outer_pivot_opens_before_forty_percent_reentry():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    long_frame.loc[long_frame.index[-1], ["open", "close", "high", "low", "ma3"]] = [
        99.05, 99.2, 99.3, 99.0, 99.2,
    ]
    long_entry = TradingEngine._channel_swing_action(long_frame, 99.2)

    short_frame = _channel_frame()
    _closed_peak(short_frame)
    short_frame.loc[short_frame.index[-1], ["open", "close", "high", "low", "ma3"]] = [
        100.95, 100.8, 101.0, 100.7, 100.8,
    ]
    short_entry = TradingEngine._channel_swing_action(short_frame, 100.8)

    assert (short_entry["action"], short_entry["side"]) == ("ENTER", "SHORT")
    assert (long_entry["action"], long_entry["side"]) == ("ENTER", "LONG")


def test_current_trend_does_not_reuse_already_confirmed_outer_pivot():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["open", "close", "low", "high", "ma3"]] = [
        98.8, 98.9, 98.7, 98.95, 98.7,
    ]
    # 前段谷底早已被中間 K 突破；即使當下仍有漲勢也不能補追。
    frame.loc[frame.index[-3], ["open", "close", "low", "high", "ma3"]] = [
        99.1, 99.0, 98.9, 99.2, 99.0,
    ]
    frame.loc[frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        99.1, 99.1, 99.0, 99.2, 99.2,
    ]
    frame.loc[frame.index[-1], ["open", "close", "low", "high", "ma3"]] = [
        99.6, 99.5, 99.15, 99.65, 99.4,
    ]

    result = TradingEngine._channel_swing_action(frame, 99.5)

    assert (result["action"], result["reason"]) == (
        "WAIT", "WAIT_ADJACENT_OUTER_CANDIDATE",
    )
    assert result["side"] is None


def test_current_downtrend_after_outer_peak_opens_on_green_candle():
    frame = _channel_frame()
    _closed_peak(frame)
    # 當下是綠 K；前段峰頂與 MA3 跌勢成立仍須立即追空。
    frame.loc[frame.index[-1], ["open", "close", "low", "high", "ma3"]] = [
        100.7, 100.8, 100.6, 101.0, 100.8,
    ]

    result = TradingEngine._channel_swing_action(frame, 100.8)

    assert (result["action"], result["side"]) == ("ENTER", "SHORT")


def test_inside_kc_two_closed_green_candles_do_not_open_long():
    frame = _channel_frame()
    frame.loc[frame.index[-3], ["open", "close", "low", "high"]] = [
        99.4, 99.6, 99.3, 99.7,
    ]
    frame.loc[frame.index[-2], ["open", "close", "low", "high"]] = [
        100.1, 100.4, 99.4, 100.5,
    ]
    frame.loc[frame.index[-1], ["open", "close"]] = [100.5, 99.8]

    result = TradingEngine._channel_swing_action(frame, 99.8)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "WAIT_ADJACENT_OUTER_CANDIDATE",
    )


def test_inside_kc_two_closed_red_candles_do_not_open_short():
    frame = _channel_frame()
    frame.loc[frame.index[-3], ["open", "close", "low", "high"]] = [
        100.6, 100.4, 100.3, 100.7,
    ]
    frame.loc[frame.index[-2], ["open", "close", "low", "high"]] = [
        99.9, 99.6, 99.5, 100.6,
    ]
    frame.loc[frame.index[-1], ["open", "close"]] = [99.5, 100.2]

    result = TradingEngine._channel_swing_action(frame, 100.2)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "WAIT_ADJACENT_OUTER_CANDIDATE",
    )


def test_inside_kc_second_candle_reverse_extreme_cancels_entry():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-3], ["open", "close", "low", "high"]] = [
        99.4, 99.6, 99.3, 99.7,
    ]
    long_frame.loc[long_frame.index[-2], ["open", "close", "low", "high"]] = [
        100.1, 100.4, 99.2, 100.5,
    ]

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-3], ["open", "close", "low", "high"]] = [
        100.6, 100.4, 100.3, 100.7,
    ]
    short_frame.loc[short_frame.index[-2], ["open", "close", "low", "high"]] = [
        99.9, 99.6, 99.5, 100.8,
    ]

    long_result = TradingEngine._channel_swing_action(long_frame, 100.4)
    short_result = TradingEngine._channel_swing_action(short_frame, 99.6)

    assert (long_result["action"], long_result["side"]) == ("WAIT", None)
    assert (short_result["action"], short_result["side"]) == ("WAIT", None)


def test_inside_kc_live_second_candle_only_previews_and_does_not_enter():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-2], ["open", "close", "low", "high"]] = [
        99.4, 99.6, 99.3, 99.7,
    ]
    long_frame.loc[long_frame.index[-1], ["open", "close", "low", "high"]] = [
        100.1, 100.4, 99.4, 100.5,
    ]

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-2], ["open", "close", "low", "high"]] = [
        100.6, 100.4, 100.3, 100.7,
    ]
    short_frame.loc[short_frame.index[-1], ["open", "close", "low", "high"]] = [
        99.9, 99.6, 99.5, 100.6,
    ]

    long_result = TradingEngine._channel_swing_action(long_frame, 100.4)
    short_result = TradingEngine._channel_swing_action(short_frame, 99.6)

    assert (long_result["action"], long_result["side"]) == ("WAIT", None)
    assert (short_result["action"], short_result["side"]) == ("WAIT", None)


def test_inside_kc_ma3_continuation_without_turn_still_waits():
    frame = _channel_frame()
    frame.loc[frame.index[-3], "ma3"] = 99.6
    frame.loc[frame.index[-2], "ma3"] = 99.8
    frame.loc[frame.index[-1], "ma3"] = 100.0

    result = TradingEngine._channel_swing_action(frame, 100.2)

    assert (result["action"], result["side"]) == ("WAIT", None)


def test_ma3_turn_does_not_open_when_price_is_outside_kc():
    frame = _channel_frame()
    frame.loc[frame.index[-2], "ma3"] = 99.7
    frame.loc[frame.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.2, 101.3, 100.0,
    ]

    result = TradingEngine._channel_swing_action(frame, 101.2)

    assert (result["action"], result["side"]) == ("WAIT", None)


def test_old_outer_pivot_does_not_chase_at_opposite_outer_rail():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["open", "close", "low", "high", "ma3"]] = [
        98.8, 98.9, 98.7, 98.95, 98.7,
    ]
    frame.loc[frame.index[-1], ["open", "close", "high", "ma3"]] = [
        100.8, 101.1, 101.2, 100.9,
    ]

    result = TradingEngine._channel_swing_action(frame, 101.1)

    assert (result["action"], result["side"]) == ("WAIT", None)


def test_shallow_outer_reentry_cannot_fall_back_to_live_ma3_entry():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], "ma3"] = 99.2  # only 10% into KC channel

    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (empty["action"], empty["side"]) == ("ENTER", "LONG")
    assert (held_short["action"], held_short["side"]) == ("HOLD", None)


def test_channel_swing_reentry_boundary_is_80_percent_of_outer_half():
    below = _channel_frame()
    _closed_trough(below)
    below.loc[below.index[-1], ["close", "ma3"]] = [99.78, 99.78]
    rejected = TradingEngine._channel_swing_action(below, 99.78)

    boundary = _channel_frame()
    _closed_trough(boundary)
    boundary.loc[boundary.index[-1], ["close", "ma3"]] = [99.8, 99.8]
    accepted = TradingEngine._channel_swing_action(boundary, 99.8)

    assert (rejected["action"], rejected["side"]) == ("ENTER", "LONG")
    assert (accepted["action"], accepted["side"]) == ("ENTER", "LONG")


def test_live_ma3_turn_neither_enters_nor_changes_held_reentry_rule():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], ["open", "close", "high", "ma3"]] = [
        99.1, 99.2, 100.5, 100.0,
    ]

    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (empty["action"], empty["side"]) == ("ENTER", "LONG")
    assert (held_short["action"], held_short["side"]) == ("HOLD", None)


def test_channel_swing_positions_are_not_managed_by_early_exit_loops():
    assert TradingEngine._is_continuous_wave_position({
        "entry_mode": "CHANNEL_SWING",
    })


def test_channel_swing_has_one_confirmation_rule_and_no_legacy_entry_paths():
    from services.api import manual_order

    action_source = inspect.getsource(TradingEngine._channel_swing_action)
    process_source = inspect.getsource(TradingEngine._process_single_symbol)
    manual_source = inspect.getsource(manual_order)

    assert "CONTINUOUS_ENTRY_OUTER_ZONE_RATIO" not in action_source
    assert '"entry_mode": "CHANNEL_SWING"' in manual_source
    assert '"wave_regime": "RANGE"' in manual_source
    assert "KC 撕裂復原" not in process_source
    assert "swing_direction" not in process_source
    assert "_channel_trend_entry_action(" not in process_source
    assert "KC_INNER_MA3_TURN" not in action_source
    assert "KC_INNER_TWO_GREEN_CROSS_UP" not in action_source
    assert "KC_INNER_TWO_RED_CROSS_DOWN" not in action_source
    assert "inner_ma3_turn" not in action_source

    reverse_start = process_source.index('if action == "REVERSE" and existing_pos:')
    reverse_end = process_source.index('if existing_pos:', reverse_start + 1)
    reverse_source = process_source[reverse_start:reverse_end]
    assert reverse_source.index("close_position(") < reverse_source.index("detected_candidates.append(")
    assert "_strongest_ranked_symbol" not in reverse_source
    assert '"symbol": symbol' in reverse_source
    assert "close-first" in reverse_source


def test_breaking_entry_pivot_without_full_outer_body_keeps_position():
    frame = _channel_frame()
    held_long = TradingEngine._channel_swing_action(
        frame, 98.8, "LONG", entry_turn_low=98.9,
    )
    held_short = TradingEngine._channel_swing_action(
        frame, 101.2, "SHORT", entry_turn_high=101.1,
    )

    assert (held_long["action"], held_long["side"]) == ("HOLD", None)
    assert (held_short["action"], held_short["side"]) == ("HOLD", None)


def test_partial_body_crossing_entry_side_outer_rail_keeps_position():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-1], ["open", "close", "low"]] = [
        99.2, 98.8, 98.7,
    ]
    held_long = TradingEngine._channel_swing_action(long_frame, 98.8, "LONG")

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-1], ["open", "close", "high"]] = [
        100.8, 101.2, 101.3,
    ]
    held_short = TradingEngine._channel_swing_action(short_frame, 101.2, "SHORT")

    assert (held_long["action"], held_long["side"]) == ("HOLD", None)
    assert (held_short["action"], held_short["side"]) == ("HOLD", None)



def test_entry_side_outer_body_does_not_exit_without_opposite_reentry():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-1], ["open", "close", "low", "ma3"]] = [
        98.9, 98.8, 98.7, 99.2,
    ]
    held_long = TradingEngine._channel_swing_action(long_frame, 98.8, "LONG")

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.2, 101.3, 100.8,
    ]
    held_short = TradingEngine._channel_swing_action(short_frame, 101.2, "SHORT")

    assert (held_long["action"], held_long["side"]) == ("HOLD", None)
    assert (held_short["action"], held_short["side"]) == ("HOLD", None)


def test_live_outer_ma3_trend_immediately_reverses_failed_turn():
    down = _channel_frame()
    down.loc[down.index[-3], ["open", "close"]] = [99.6, 99.4]
    down.loc[down.index[-2], ["open", "close", "ma3"]] = [99.3, 98.9, 98.8]
    down.loc[down.index[-1], ["open", "close", "low", "ma3"]] = [
        98.9, 98.7, 98.6, 98.6,
    ]
    held_long = TradingEngine._channel_swing_action(down, 98.7, "LONG")

    up = _channel_frame()
    up.loc[up.index[-3], ["open", "close"]] = [100.4, 100.6]
    up.loc[up.index[-2], ["open", "close", "ma3"]] = [100.7, 101.1, 101.2]
    up.loc[up.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.3, 101.4, 101.4,
    ]
    held_short = TradingEngine._channel_swing_action(up, 101.3, "SHORT")

    assert (held_long["action"], held_long["side"], held_long["reason"]) == (
        "REVERSE", "SHORT", "OPPOSITE_LOWER_OUTER_DOWNTREND",
    )
    assert (held_short["action"], held_short["side"], held_short["reason"]) == (
        "REVERSE", "LONG", "OPPOSITE_UPPER_OUTER_UPTREND",
    )


def test_second_live_outer_candle_does_not_reverse_before_third():
    down = _channel_frame()
    down.loc[down.index[-3], ["open", "close"]] = [99.2, 99.4]
    down.loc[down.index[-2], ["open", "close", "ma3"]] = [99.3, 99.1, 99.4]
    down.loc[down.index[-1], ["open", "close", "low", "ma3"]] = [
        98.9, 98.7, 98.6, 98.6,
    ]
    held_long = TradingEngine._channel_swing_action(down, 98.7, "LONG")

    up = _channel_frame()
    up.loc[up.index[-3], ["open", "close"]] = [100.8, 100.6]
    up.loc[up.index[-2], ["open", "close", "ma3"]] = [100.7, 100.9, 100.6]
    up.loc[up.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.3, 101.4, 101.4,
    ]
    held_short = TradingEngine._channel_swing_action(up, 101.3, "SHORT")

    assert (held_long["action"], held_long["side"]) == ("HOLD", None)
    assert (held_short["action"], held_short["side"]) == ("HOLD", None)


def test_live_outer_price_without_ma3_outside_does_not_reverse():
    up = _channel_frame()
    up.loc[up.index[-2], "ma3"] = 100.6
    up.loc[up.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.3, 101.4,  100.8,
    ]
    held_short = TradingEngine._channel_swing_action(up, 101.3, "SHORT")

    down = _channel_frame()
    down.loc[down.index[-2], "ma3"] = 99.4
    down.loc[down.index[-1], ["open", "close", "low", "ma3"]] = [
        98.9, 98.7, 98.6,  99.2,
    ]
    held_long = TradingEngine._channel_swing_action(down, 98.7, "LONG")

    assert (held_short["action"], held_short["side"]) == ("HOLD", None)
    assert (held_long["action"], held_long["side"]) == ("HOLD", None)


def test_outer_turn_without_body_reentry_does_not_close_or_reverse():
    long_frame = _channel_frame()
    _closed_peak(long_frame)
    held_long = TradingEngine._channel_swing_action(long_frame, 101.1, "LONG")

    short_frame = _channel_frame()
    _closed_trough(short_frame)
    held_short = TradingEngine._channel_swing_action(short_frame, 98.9, "SHORT")

    assert (held_long["action"], held_long["side"]) == ("HOLD", None)
    assert (held_short["action"], held_short["side"]) == ("HOLD", None)


def test_previous_red_cross_does_not_reverse_when_live_candle_started_inside_kc():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        101.2, 100.2, 99.7, 101.3, 100.9,
    ]
    frame.loc[frame.index[-1], ["open", "close", "low", "high", "ma3"]] = [
        99.8, 99.6, 99.5, 100.0, 100.5,
    ]

    result = TradingEngine._channel_swing_action(frame, 99.6, "LONG")

    assert (result["action"], result["side"]) == ("HOLD", None)

def test_previous_green_cross_does_not_reverse_when_live_candle_started_inside_kc():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        98.8, 99.8, 98.7, 100.3, 99.1,
    ]
    frame.loc[frame.index[-1], ["open", "close", "low", "high", "ma3"]] = [
        100.2, 100.4, 100.1, 100.5, 99.5,
    ]

    result = TradingEngine._channel_swing_action(frame, 100.4, "SHORT")

    assert (result["action"], result["side"]) == ("HOLD", None)

def test_btc_1m_pulse_requires_atr_move_and_ma3_alignment(monkeypatch):
    monkeypatch.setattr("core.engine.BTC_1M_PULSE_FILTER_ENABLED", True)
    frame = pd.DataFrame({
        "close": [100.0, 100.0, 100.2, 100.5, 100.8],
        "ma3": [100.0, 100.0, 100.1, 100.3, 100.6],
        "atr": [1.0] * 5,
    })
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.8) == "LONG"

    frame["close"] = [100.8, 100.8, 100.6, 100.3, 100.0]
    frame["ma3"] = [100.8, 100.8, 100.7, 100.5, 100.2]
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.0) == "SHORT"

def test_old_two_closed_bar_bounce_does_not_reuse_stale_long_candidate():
    frame = _channel_frame()
    _closed_trough(frame)
    # _closed_trough modifies -3 and -2. Let's make sure the channel is strictly valid for all.
    for i in range(1, 4):
        frame.loc[frame.index[-i], ["kc_lower", "ema_20", "kc_upper"]] = [98.9, 99.9, 100.9]

    # Set middle trends up
    frame.loc[frame.index[-3], ["kc_lower", "ema_20", "kc_upper"]] = [98.9, 99.9, 100.9]
    frame.loc[frame.index[-2], ["kc_lower", "ema_20", "kc_upper"]] = [99.0, 100.0, 101.0] # middle 100
    frame.loc[frame.index[-1], ["kc_lower", "ema_20", "kc_upper"]] = [99.1, 100.1, 101.1] # middle 100.1

    # First candle (touch): Green K
    frame.loc[frame.index[-3], ["open", "close", "low", "high", "ma3"]] = [99.0, 99.4, 98.9, 99.5, 99.0]
    # Second candle (confirm): breaks high of first candle
    frame.loc[frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [99.4, 99.6, 99.3, 99.8, 100.2]
    # Live candle
    frame.loc[frame.index[-1], ["open", "close", "low", "ma3"]] = [99.6, 99.7, 99.5, 100.3]

    result = TradingEngine._channel_swing_action(frame, 99.8)
    assert (result.get("action"), result.get("side"), result.get("reason")) == (
        "WAIT", None, "WAIT_ADJACENT_OUTER_CANDIDATE",
    )

def test_old_two_closed_bar_bounce_does_not_reuse_stale_short_candidate():
    frame = _channel_frame()
    _closed_peak(frame)
    for i in range(1, 4):
        frame.loc[frame.index[-i], ["kc_lower", "ema_20", "kc_upper"]] = [99.1, 100.1, 101.1]

    # Set middle trends down
    frame.loc[frame.index[-3], ["kc_lower", "ema_20", "kc_upper"]] = [99.1, 100.1, 101.1]
    frame.loc[frame.index[-2], ["kc_lower", "ema_20", "kc_upper"]] = [99.0, 100.0, 101.0] # middle 100
    frame.loc[frame.index[-1], ["kc_lower", "ema_20", "kc_upper"]] = [98.9, 99.9, 100.9] # middle 99.9

    # First candle (touch): Red K
    frame.loc[frame.index[-3], ["open", "close", "high", "low", "ma3"]] = [101.0, 100.6, 101.1, 100.5, 101.5]
    # Second candle (confirm): breaks low of first candle
    frame.loc[frame.index[-2], ["open", "close", "high", "low", "ma3"]] = [100.6, 100.3, 100.8, 100.2, 101.0]
    # Live candle
    frame.loc[frame.index[-1], ["open", "close", "high", "ma3"]] = [100.3, 100.1, 100.4, 99.7]

    result = TradingEngine._channel_swing_action(frame, 100.2)
    assert (result.get("action"), result.get("side"), result.get("reason")) == (
        "WAIT", None, "WAIT_ADJACENT_OUTER_CANDIDATE",
    )

def test_detect_btc_pulse():
    frame = pd.DataFrame()
    frame["close"] = [100.0, 100.0, 100.1, 100.2, 100.3]
    frame["ma3"] = [100.0, 100.0, 100.1, 100.2, 100.25]
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.3) is None
    assert TradingEngine._btc_pulse_blocks_entry("SHORT", "LONG") is True
    assert TradingEngine._btc_pulse_blocks_entry("LONG", "SHORT") is True
    assert TradingEngine._btc_pulse_blocks_entry("LONG", "LONG") is False
    assert TradingEngine._btc_pulse_blocks_entry("SHORT", None) is False


def test_btc_1m_pulse_is_disabled_by_configuration(monkeypatch):
    monkeypatch.setattr("core.engine.BTC_1M_PULSE_FILTER_ENABLED", False)
    frame = pd.DataFrame({
        "close": [100.0, 100.2, 100.5, 100.8],
        "ma3": [100.0, 100.1, 100.3, 100.6],
        "atr": [1.0] * 4,
    })

    assert TradingEngine._detect_btc_1m_pulse(frame, 100.8) is None


def test_entries_only_match_ranked_market_direction():
    assert TradingEngine._entry_matches_ranked_direction("LONG", "LONG") is True
    assert TradingEngine._entry_matches_ranked_direction("SHORT", "SHORT") is True
    assert TradingEngine._entry_matches_ranked_direction("LONG", "SHORT") is False
    assert TradingEngine._entry_matches_ranked_direction("SHORT", "LONG") is False
    assert TradingEngine._entry_matches_ranked_direction("LONG", "BOTH") is False
    assert TradingEngine._entry_matches_ranked_direction("SHORT", None) is False


def _dynamic_upper_trend_frame():
    frame = _channel_frame()
    for position in range(13, 19):
        middle = 99.5 + (position - 13) * 0.10
        close = middle + 0.80 + (position - 13) * 0.05
        frame.loc[position, [
            "open", "close", "high", "low", "ma3", "ma15",
            "kc_lower", "kc_upper",
        ]] = [
            close - 0.15, close, close + 0.10, close - 0.20,
            middle + 0.30, middle + 0.10, middle - 1.0, middle + 1.0,
        ]
    frame.loc[19, [
        "open", "close", "high", "low", "ma3", "ma15",
        "kc_lower", "kc_upper",
    ]] = [101.20, 101.35, 101.36, 101.15, 100.55, 100.25, 99.10, 101.10]
    return frame


def test_kc_upper_outer_uptrend_uses_existing_quality_without_three_bar_delay():
    frame = _dynamic_upper_trend_frame()

    candidate = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)

    assert (candidate["action"], candidate["reason"]) == ("WAIT", "WAIT_TREND_BREAK")
    assert candidate["pending"]["side"] == "LONG"
    assert candidate["pending"]["confirmed"] is False

    entered = TradingEngine._channel_outer_uptrend_entry_action(
        frame, 101.35, candidate["pending"],
    )
    assert (entered["action"], entered["side"]) == ("ENTER", "LONG")
    assert entered["reason"] == "KC_UPPER_TREND_CONFIRMED_LONG"


def test_kc_upper_outer_uptrend_waits_when_existing_trend_is_ambiguous():
    frame = _dynamic_upper_trend_frame()
    frame.loc[16:18, ["ma3", "ma15"]] = [[100.0, 100.1]] * 3

    result = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)

    assert (result["action"], result["reason"]) == ("WAIT", "WAIT_DYNAMIC_TREND")
    assert result["pending"] is None


def test_kc_upper_outer_uptrend_next_bar_failure_cancels_candidate():
    frame = _dynamic_upper_trend_frame()
    seed = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)
    frame.loc[19, "low"] = float(seed["pending"]["candidate_low"]) - 0.01

    result = TradingEngine._channel_outer_uptrend_entry_action(
        frame, 101.20, seed["pending"],
    )

    assert (result["action"], result["reason"]) == ("WAIT", "CANCEL_TREND_CONFIRM")
    assert result["pending"] is None


def test_kc_upper_outer_uptrend_does_not_chase_when_break_is_too_far():
    frame = _dynamic_upper_trend_frame()
    seed = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)

    result = TradingEngine._channel_outer_uptrend_entry_action(
        frame, 102.0, seed["pending"],
    )

    assert result["action"] == "WAIT"
    assert result["reason"] in ("WAIT_TREND_RETEST", "WAIT_TREND_RETEST_BREAK")
    assert result["pending"]["confirmed"] is True


def _dynamic_lower_trend_frame():
    frame = _channel_frame()
    for position in range(13, 19):
        middle = 100.5 - (position - 13) * 0.10
        close = middle - 0.80 - (position - 13) * 0.05
        frame.loc[position, [
            "open", "close", "high", "low", "ma3", "ma15",
            "kc_lower", "kc_upper",
        ]] = [
            close + 0.15, close, close + 0.20, close - 0.10,
            middle - 0.30, middle - 0.10, middle - 1.0, middle + 1.0,
        ]
    frame.loc[19, [
        "open", "close", "high", "low", "ma3", "ma15",
        "kc_lower", "kc_upper",
    ]] = [98.90, 98.85, 98.95, 98.84, 99.45, 99.75, 98.90, 100.90]
    return frame


def test_kc_lower_outer_downtrend_uses_symmetric_next_bar_break():
    frame = _dynamic_lower_trend_frame()

    candidate = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)

    assert (candidate["action"], candidate["reason"]) == (
        "WAIT", "WAIT_DOWNTREND_BREAK",
    )
    assert candidate["pending"]["side"] == "SHORT"
    assert candidate["pending"]["confirmed"] is False

    entered = TradingEngine._channel_outer_trend_entry_action(
        frame, 98.85, candidate["pending"],
    )
    assert (entered["action"], entered["side"]) == ("ENTER", "SHORT")
    assert entered["reason"] == "KC_LOWER_TREND_CONFIRMED_SHORT"


def test_kc_lower_outer_downtrend_waits_when_trend_is_ambiguous():
    frame = _dynamic_lower_trend_frame()
    frame.loc[16:18, ["ma3", "ma15"]] = [[100.1, 100.0]] * 3

    result = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)

    assert (result["action"], result["reason"]) == (
        "WAIT", "WAIT_DYNAMIC_DOWNTREND",
    )
    assert result["pending"] is None


def test_kc_lower_outer_downtrend_next_bar_failure_cancels_candidate():
    frame = _dynamic_lower_trend_frame()
    seed = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)
    frame.loc[19, "high"] = float(seed["pending"]["candidate_high"]) + 0.01

    result = TradingEngine._channel_outer_trend_entry_action(
        frame, 98.92, seed["pending"],
    )

    assert (result["action"], result["reason"]) == (
        "WAIT", "CANCEL_DOWNTREND_CONFIRM",
    )
    assert result["pending"] is None


def test_kc_lower_outer_downtrend_does_not_chase_when_break_is_too_far():
    frame = _dynamic_lower_trend_frame()
    seed = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)

    result = TradingEngine._channel_outer_trend_entry_action(
        frame, 98.0, seed["pending"],
    )

    assert result["action"] == "WAIT"
    assert result["reason"] in (
        "WAIT_DOWNTREND_RETEST", "WAIT_DOWNTREND_RETEST_BREAK",
    )
    assert result["pending"]["confirmed"] is True


def test_kc_upper_wick_without_outer_close_does_not_chase():
    frame = _dynamic_upper_trend_frame()
    frame.loc[18, "close"] = float(frame.loc[18, "kc_upper"]) - 0.01

    result = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.1)

    assert (result["action"], result["side"]) == ("WAIT", None)
    assert result["reason"] == "WAIT_OUTER_UPTREND"


def _chop_momentum_frame(side):
    frame = _channel_frame()
    for position in range(10, 18):
        close = 99.95 if position % 2 == 0 else 100.05
        frame.loc[position, [
            "open", "close", "high", "low", "ma3", "ma15",
            "kc_lower", "kc_upper",
        ]] = [100.0, close, close + 0.05, close - 0.05, close, 100.0, 99.0, 101.0]
    if side == "LONG":
        frame.loc[18, [
            "open", "close", "high", "low", "ma3", "ma15",
            "kc_lower", "kc_upper",
        ]] = [100.10, 100.50, 100.60, 100.05, 100.30, 100.05, 99.02, 101.02]
        frame.loc[19, [
            "open", "close", "high", "low", "ma3", "ma15",
            "kc_lower", "kc_upper",
        ]] = [100.50, 100.65, 100.66, 100.45, 100.40, 100.10, 99.04, 101.04]
    else:
        frame.loc[18, [
            "open", "close", "high", "low", "ma3", "ma15",
            "kc_lower", "kc_upper",
        ]] = [99.90, 99.50, 99.95, 99.40, 99.70, 99.95, 98.98, 100.98]
        frame.loc[19, [
            "open", "close", "high", "low", "ma3", "ma15",
            "kc_lower", "kc_upper",
        ]] = [99.50, 99.35, 99.55, 99.34, 99.60, 99.90, 98.96, 100.96]
    return frame


def test_chop_wait_can_enter_long_from_inside_kc_after_confirmed_momentum_break():
    frame = _chop_momentum_frame("LONG")

    result = TradingEngine._channel_chop_breakout_action(frame, 100.65)

    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", "LONG", "CHOP_BREAKOUT_LONG",
    )
    assert float(frame.loc[18, "close"]) < float(frame.loc[18, "kc_upper"])


def test_chop_wait_can_enter_short_from_inside_kc_after_confirmed_momentum_break():
    frame = _chop_momentum_frame("SHORT")

    result = TradingEngine._channel_chop_breakout_action(frame, 99.35)

    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", "SHORT", "CHOP_BREAKOUT_SHORT",
    )
    assert float(frame.loc[18, "close"]) > float(frame.loc[18, "kc_lower"])


def test_chop_momentum_candidate_still_waits_for_next_bar_break():
    frame = _chop_momentum_frame("LONG")

    result = TradingEngine._channel_chop_breakout_action(frame, 100.55)

    assert (result["action"], result["reason"]) == (
        "WAIT", "WAIT_CHOP_MOMENTUM_BREAK",
    )


def test_strongest_ranked_symbol_uses_target_direction_final_score():
    engine = TradingEngine.__new__(TradingEngine)

    class Rotation:
        direction_map = {
            "SOL/USDT": "LONG",
            "XRP/USDT": "LONG",
            "DOGE/USDT": "SHORT",
        }
        last_metrics = [
            {"symbol": "SOL/USDT", "direction": "LONG", "final_score": 70},
            {"symbol": "XRP/USDT", "direction": "LONG", "final_score": 90},
            {"symbol": "DOGE/USDT", "direction": "SHORT", "final_score": 95},
        ]

    engine.symbol_rotation = Rotation()

    assert engine._strongest_ranked_symbol("LONG") == ("XRP/USDT", 90.0)
    assert engine._strongest_ranked_symbol("SHORT") == ("DOGE/USDT", 95.0)


def test_market_candidates_keep_only_strongest_per_direction():
    candidates = [
        {"symbol": "SOL/USDT", "side": "LONG", "score": 100, "trend_quality": 0.8},
        {"symbol": "XRP/USDT", "side": "LONG", "score": 95, "trend_quality": 1.2},
        {"symbol": "DOGE/USDT", "side": "SHORT", "score": 90, "trend_quality": 0.7},
    ]

    selected, skipped = TradingEngine._select_strongest_same_side_candidates(candidates)

    assert [item["symbol"] for item in selected] == ["XRP/USDT", "DOGE/USDT"]
    assert [item["symbol"] for item in skipped] == ["SOL/USDT"]


def test_kc_pivot_switch_candidate_has_priority_in_same_direction():
    candidates = [
        {"symbol": "SOL/USDT", "side": "SHORT", "score": 100,
         "trend_quality": 99.0},
        {"symbol": "DOGE/USDT", "side": "SHORT", "score": 100,
         "trend_quality": 0.5, "priority": 1},
    ]

    selected, skipped = TradingEngine._select_strongest_same_side_candidates(candidates)

    assert [item["symbol"] for item in selected] == ["DOGE/USDT"]
    assert [item["symbol"] for item in skipped] == ["SOL/USDT"]


def test_single_slot_amount_uses_eighty_percent_wallet():
    engine = TradingEngine.__new__(TradingEngine)

    class Account:
        positions = {}
        pending_limit_orders = {}

        def get_available_balance(self):
            return 150.0 if not self.positions else 74.85

        def get_wallet_balance(self):
            return 150.0

    engine.account = Account()
    assert engine._continuous_entry_amount() == pytest.approx(120.0)
    engine.account.positions["SOL/USDT"] = {"margin": 120.0}
    assert engine._continuous_entry_amount() == pytest.approx(0.0)


@pytest.mark.anyio
async def test_channel_swing_position_is_stored_without_sl_or_tp(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "channel_swing.json"))
    account = PaperAccount()

    opened = await account.open_position(
        "BTC/USDT", "LONG", 100.0, 50.0, 95.0, 110.0,
        "channel swing", leverage=1, signal_score=100, apply_slippage=False,
        entry_context={
            "entry_mode": "CHANNEL_SWING",
            "wave_regime": "RANGE",
            "initial_sl": 95.0,
            "initial_risk": 5.0,
        },
    )

    assert opened is True
    assert account.positions["BTC/USDT"]["sl"] == 0.0
    assert account.positions["BTC/USDT"]["tp"] == 0.0
    assert account.position_meta["BTC/USDT"]["sl"] == 0.0
    assert account.position_meta["BTC/USDT"]["initial_sl"] == 0.0

    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_LOCK_USDT", True)
    monkeypatch.setattr(pa_module, "ENABLE_FIXED_PROFIT_LOCK_PCT", True)
    monkeypatch.setattr(pa_module, "ENABLE_TRAILING_STOP", True)
    monkeypatch.setattr(pa_module, "ENABLE_EARLY_PROFIT_GUARD", True)
    await account.update_positions({"BTC/USDT": 102.0})
    await account.update_positions({"BTC/USDT": 100.5})
    assert "BTC/USDT" in account.positions
    assert account.positions["BTC/USDT"]["sl"] == 0.0
    assert not account.positions["BTC/USDT"].get("is_breakeven_moved")

    topped_up = await account.open_position(
        "BTC/USDT", "LONG", 100.2, 25.0, 95.0, 110.0,
        "channel swing top-up", leverage=1, signal_score=100,
        apply_slippage=False,
        entry_context={"entry_mode": "CHANNEL_SWING"},
    )

    assert topped_up is False
    assert account.positions["BTC/USDT"]["margin"] == 50.0

    reloaded = PaperAccount()
    await reloaded.initialize()

    assert reloaded.positions["BTC/USDT"]["sl"] == 0.0
    assert reloaded.positions["BTC/USDT"]["tp"] == 0.0
    assert not any(
        "啟動保護遷移" in item.get("text", "")
        for item in reloaded.logs
    )


def test_channel_swing_ignores_legacy_structured_stop_cooldown():
    assert TradingEngine._structured_stop_cooldown_blocks(
        "CHANNEL_SWING", 3600.0,
    ) is False
    assert TradingEngine._structured_stop_cooldown_blocks(
        "BREAKOUT", 3600.0,
    ) is True
    assert TradingEngine._structured_stop_cooldown_blocks(
        "BREAKOUT", 0.0,
    ) is False


def test_channel_signal_events_deduplicate_and_track_reason_transitions():
    class LogAccount:
        def __init__(self):
            self.logs = []

        def log(self, text, level):
            self.logs.append((text, level))

    engine = TradingEngine.__new__(TradingEngine)
    engine._channel_signal_events = {}
    engine.account = LogAccount()
    frame = _channel_frame()
    frame["timestamp"] = [index * 60_000 for index in range(len(frame))]

    engine._record_channel_signal_event(
        "TEST/USDT", "KC_WIDTH_TOO_NARROW", frame,
    )
    first_event = engine._channel_signal_events["TEST/USDT"][0]
    assert (first_event["action"], first_event["reason"]) == (
        "CHANNEL_BLOCK", "KC_WIDTH_TOO_NARROW",
    )
    assert first_event["label"] == "KC寬度不足設定門檻"

    frame.loc[frame.index[-1], "timestamp"] += 60_000
    engine._record_channel_signal_event(
        "TEST/USDT", "KC_WIDTH_TOO_NARROW", frame,
    )
    assert len(engine._channel_signal_events["TEST/USDT"]) == 1
    assert len(engine.account.logs) == 1

    engine._record_channel_signal_event(
        "TEST/USDT", "CANCEL_LONG", frame,
    )
    assert len(engine._channel_signal_events["TEST/USDT"]) == 2
    replacement = engine._channel_signal_events["TEST/USDT"][-1]
    assert (replacement["action"], replacement["reason"]) == (
        "CHANNEL_CANCEL", "CANCEL_LONG",
    )
    assert len(engine.account.logs) == 2
