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


def test_channel_swing_enters_only_after_closed_turn_candle_and_next_breakout():
    frame = _channel_frame()
    _closed_trough(frame)

    low = TradingEngine._channel_swing_action(frame, 99.2)

    frame = _channel_frame()
    _closed_peak(frame)
    high = TradingEngine._channel_swing_action(frame, 100.8)

    assert low == {
        "action": "ENTER", "side": "LONG",
        "kc_upper": 101.0, "kc_lower": 99.0, "reason": "",
        "turn_low": 98.7, "turn_high": None,
    }
    assert high == {
        "action": "ENTER", "side": "SHORT",
        "kc_upper": 101.0, "kc_lower": 99.0, "reason": "",
        "turn_low": None, "turn_high": 101.3,
    }


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


def test_body_only_slightly_inside_half_channel_cannot_bypass_outer_depth():
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

    assert (rejected_long["action"], rejected_long["reason"]) == (
        "WAIT", "V_TOO_CLOSE_KC",
    )
    assert (rejected_short["action"], rejected_short["reason"]) == (
        "WAIT", "V_TOO_CLOSE_KC",
    )


def test_partial_body_inside_does_not_bypass_shallow_outer_depth():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        98.95, 99.2, 98.9, 99.25, 98.7,
    ]
    frame.loc[frame.index[-1], ["close", "ma3"]] = [100.0, 100.0]

    result = TradingEngine._channel_swing_action(frame, 100.0)

    assert (result["action"], result["reason"]) == ("WAIT", "V_TOO_CLOSE_KC")


def test_invalid_shallow_candidate_does_not_hide_earlier_valid_turn():
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
    assert long_turn["turn_low"] == pytest.approx(98.7)
    assert (short_turn["action"], short_turn["side"]) == ("ENTER", "SHORT")
    assert short_turn["turn_high"] == pytest.approx(101.3)


def test_channel_swing_accepts_multiple_green_or_red_candles_as_one_turn_leg():
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

    assert (long_turn["action"], long_turn["side"]) == ("ENTER", "LONG")
    assert long_turn["turn_low"] == pytest.approx(98.7)
    assert (short_turn["action"], short_turn["side"]) == ("ENTER", "SHORT")
    assert short_turn["turn_high"] == pytest.approx(101.3)


def test_channel_swing_keeps_multi_candle_leg_through_opposite_color_candle():
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

    assert (result["action"], result["side"]) == ("ENTER", "LONG")


def test_empty_slot_enters_when_price_and_ma3_trend_outside_kc():
    up = _channel_frame()
    up.loc[up.index[-3], "ma3"] = 100.5
    up.loc[up.index[-2], "ma3"] = 100.9
    up.loc[up.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.4, 101.5, 101.3,
    ]
    long_entry = TradingEngine._channel_swing_action(up, 101.4)

    down = _channel_frame()
    down.loc[down.index[-3], "ma3"] = 99.5
    down.loc[down.index[-2], "ma3"] = 99.1
    down.loc[down.index[-1], ["open", "close", "low", "ma3"]] = [
        98.9, 98.6, 98.5, 98.7,
    ]
    short_entry = TradingEngine._channel_swing_action(down, 98.6)

    assert (long_entry["action"], long_entry["side"]) == ("ENTER", "LONG")
    assert long_entry["reason"] == "UPPER_OUTER_TREND"
    assert long_entry["turn_low"] is None
    assert (short_entry["action"], short_entry["side"]) == ("ENTER", "SHORT")
    assert short_entry["reason"] == "LOWER_OUTER_TREND"
    assert short_entry["turn_high"] is None


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
    assert result["reason"] == "COUNTERTREND_LONG_BLOCKED"
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
    assert result["reason"] == "COUNTERTREND_LONG_BLOCKED"


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
    assert result["reason"] == "COUNTERTREND_SHORT_BLOCKED"
    assert result["turn_low"] is None
    assert result["turn_high"] is None


def test_channel_swing_holds_between_entry_and_opposite_edge():
    frame = _channel_frame()

    assert TradingEngine._channel_swing_action(frame, 100.0, "LONG")["action"] == "HOLD"
    assert TradingEngine._channel_swing_action(frame, 100.0, "SHORT")["action"] == "HOLD"


def test_channel_swing_reverses_only_after_touching_opposite_rail():
    frame = _channel_frame()
    _closed_peak(frame)
    long_exit = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    frame = _channel_frame()
    _closed_trough(frame)
    short_exit = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (long_exit["action"], long_exit["side"]) == ("REVERSE", "SHORT")
    assert (short_exit["action"], short_exit["side"]) == ("REVERSE", "LONG")


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

    result = TradingEngine._channel_swing_action(frame, 98.9)

    assert result["action"] == "WAIT"
    assert result["reason"] == "WAIT_BREAK_HIGH"


def test_channel_swing_cancels_failed_trough_before_entry():
    frame = _channel_frame()
    _closed_trough(frame)

    result = TradingEngine._channel_swing_action(frame, 98.6)

    assert result["action"] == "WAIT"
    assert result["reason"] == "CANCEL_LONG"


def test_channel_swing_cancels_trough_if_confirmation_candle_wicked_below_first():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], ["low", "high"]] = [98.6, 99.3]

    result = TradingEngine._channel_swing_action(frame, 99.2)

    assert result["action"] == "WAIT"
    assert result["reason"] == "CANCEL_LONG"


def test_channel_swing_short_waits_then_cancels_if_next_candle_breaks_high():
    frame = _channel_frame()
    _closed_peak(frame)

    waiting = TradingEngine._channel_swing_action(frame, 101.1)
    frame.loc[frame.index[-1], ["low", "high"]] = [100.7, 101.4]
    cancelled = TradingEngine._channel_swing_action(frame, 100.8)

    assert waiting["action"] == "WAIT"
    assert waiting["reason"] == "WAIT_BREAK_LOW"
    assert cancelled["action"] == "WAIT"
    assert cancelled["reason"] == "CANCEL_SHORT"


def test_channel_swing_does_not_exit_before_actual_rail_touch():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "low", "high"]] = [
        101.0, 100.9, 100.8, 100.95,
    ]
    result = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    assert result["action"] == "HOLD"
    assert result["side"] is None


def test_channel_swing_confirms_on_next_break_even_after_leaving_outer_zone():
    frame = _channel_frame()
    _closed_peak(frame)

    result = TradingEngine._channel_swing_action(frame, 100.6, "LONG")

    assert result["action"] == "REVERSE"
    assert result["side"] == "SHORT"


def test_channel_swing_holds_when_v_line_is_too_close_to_kc():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-2], ["open", "ma3"]] = [98.8, 98.9]  # partial body outside, MA3 only 5% out

    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (empty["action"], empty["reason"]) == ("WAIT", "V_TOO_CLOSE_KC")
    assert (held_short["action"], held_short["reason"]) == ("HOLD", "V_TOO_CLOSE_KC")


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

    assert (long_entry["action"], long_entry["side"]) == ("ENTER", "LONG")
    assert long_entry["turn_low"] == pytest.approx(98.7)
    assert (short_entry["action"], short_entry["side"]) == ("ENTER", "SHORT")
    assert short_entry["turn_high"] == pytest.approx(101.3)


def test_current_trend_after_outer_pivot_opens_regardless_of_candle_color():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["open", "close", "low", "high", "ma3"]] = [
        98.8, 98.9, 98.7, 98.95, 98.7,
    ]
    # 當下是紅 K；只要前段谷底與 MA3 漲勢成立仍須立即追多。
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

    assert (result["action"], result["side"]) == ("ENTER", "LONG")
    assert result["turn_low"] == pytest.approx(98.7)


def test_current_downtrend_after_outer_peak_opens_on_green_candle():
    frame = _channel_frame()
    _closed_peak(frame)
    # 當下是綠 K；前段峰頂與 MA3 跌勢成立仍須立即追空。
    frame.loc[frame.index[-1], ["open", "close", "low", "high", "ma3"]] = [
        100.7, 100.8, 100.6, 101.0, 100.8,
    ]

    result = TradingEngine._channel_swing_action(frame, 100.8)

    assert (result["action"], result["side"]) == ("ENTER", "SHORT")
    assert result["turn_high"] == pytest.approx(101.3)


def test_inside_kc_move_without_prior_outer_pivot_still_does_not_open():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "ma3"]] = [99.6, 99.8, 99.7]
    frame.loc[frame.index[-1], ["open", "close", "ma3"]] = [99.8, 100.2, 100.0]

    result = TradingEngine._channel_swing_action(frame, 100.2)

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


def test_channel_swing_holds_when_ma3_reentry_is_too_shallow():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], "ma3"] = 99.2  # only 10% into KC channel

    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (empty["action"], empty["reason"]) == ("WAIT", "KC_REENTRY_TOO_SHALLOW")
    assert (held_short["action"], held_short["reason"]) == ("HOLD", "KC_REENTRY_TOO_SHALLOW")


def test_channel_swing_reentry_boundary_is_80_percent_of_outer_half():
    below = _channel_frame()
    _closed_trough(below)
    below.loc[below.index[-1], ["close", "ma3"]] = [99.78, 99.78]
    rejected = TradingEngine._channel_swing_action(below, 99.78)

    boundary = _channel_frame()
    _closed_trough(boundary)
    boundary.loc[boundary.index[-1], ["close", "ma3"]] = [99.8, 99.8]
    accepted = TradingEngine._channel_swing_action(boundary, 99.8)

    assert (rejected["action"], rejected["reason"]) == (
        "WAIT", "KC_REENTRY_TOO_SHALLOW",
    )
    assert (accepted["action"], accepted["side"]) == ("ENTER", "LONG")


def test_channel_swing_wick_inside_kc_does_not_count_as_reentry():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], ["open", "close", "high", "ma3"]] = [
        99.1, 99.2, 100.5, 100.0,
    ]

    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (empty["action"], empty["reason"]) == ("WAIT", "KC_REENTRY_TOO_SHALLOW")
    assert (held_short["action"], held_short["reason"]) == ("HOLD", "KC_REENTRY_TOO_SHALLOW")


def test_channel_swing_positions_are_not_managed_by_early_exit_loops():
    assert TradingEngine._is_continuous_wave_position({
        "entry_mode": "CHANNEL_SWING",
    })


def test_channel_swing_has_one_confirmation_rule_and_no_legacy_entry_paths():
    action_source = inspect.getsource(TradingEngine._channel_swing_action)
    process_source = inspect.getsource(TradingEngine._process_single_symbol)

    assert "CONTINUOUS_ENTRY_OUTER_ZONE_RATIO" not in action_source
    assert "KC 撕裂復原" not in process_source
    assert "swing_direction" not in process_source


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


def test_full_outer_body_exits_without_confirmed_trend():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-1], ["open", "close", "low", "ma3"]] = [
        98.9, 98.8, 98.7, 99.2,
    ]
    long_exit = TradingEngine._channel_swing_action(long_frame, 98.8, "LONG")

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.2, 101.3, 100.8,
    ]
    short_exit = TradingEngine._channel_swing_action(short_frame, 101.2, "SHORT")

    assert (long_exit["action"], long_exit["reason"]) == (
        "EXIT", "RETURNED_LOWER_OUTER",
    )
    assert (short_exit["action"], short_exit["reason"]) == (
        "EXIT", "RETURNED_UPPER_OUTER",
    )


def test_full_outer_body_reverses_only_with_confirmed_ma3_trend():
    down = _channel_frame()
    down.loc[down.index[-2], "ma3"] = 99.4
    down.loc[down.index[-1], ["open", "close", "low", "ma3"]] = [
        98.9, 98.7, 98.6, 98.6,
    ]
    reverse_short = TradingEngine._channel_swing_action(down, 98.7, "LONG")

    up = _channel_frame()
    up.loc[up.index[-2], "ma3"] = 100.6
    up.loc[up.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.3, 101.4, 101.4,
    ]
    reverse_long = TradingEngine._channel_swing_action(up, 101.3, "SHORT")

    assert (reverse_short["action"], reverse_short["side"]) == (
        "REVERSE", "SHORT",
    )
    assert reverse_short["reason"] == "FAILED_LONG_TURN_OUTER_TREND"
    assert (reverse_long["action"], reverse_long["side"]) == (
        "REVERSE", "LONG",
    )
    assert reverse_long["reason"] == "FAILED_SHORT_TURN_OUTER_TREND"


def test_channel_swing_can_chain_full_outer_body_trend_reversals():
    up = _channel_frame()
    up.loc[up.index[-2], "ma3"] = 100.6
    up.loc[up.index[-1], ["open", "close", "high", "ma3"]] = [
        101.1, 101.3, 101.4, 101.4,
    ]
    to_long = TradingEngine._channel_swing_action(up, 101.3, "SHORT")

    down = _channel_frame()
    down.loc[down.index[-2], "ma3"] = 99.4
    down.loc[down.index[-1], ["open", "close", "low", "ma3"]] = [
        98.9, 98.7, 98.6, 98.6,
    ]
    to_short = TradingEngine._channel_swing_action(down, 98.7, "LONG")

    assert (to_long["action"], to_long["side"]) == ("REVERSE", "LONG")
    assert (to_short["action"], to_short["side"]) == ("REVERSE", "SHORT")


def test_channel_swing_exits_on_qualified_opposite_turn_before_breakout():
    long_frame = _channel_frame()
    _closed_peak(long_frame)
    long_exit = TradingEngine._channel_swing_action(long_frame, 101.1, "LONG")
    short_frame = _channel_frame()
    _closed_trough(short_frame)
    short_exit = TradingEngine._channel_swing_action(short_frame, 98.9, "SHORT")
    assert (long_exit["action"], long_exit["reason"]) == ("EXIT", "UPPER_OUTER_FALLING")
    assert (short_exit["action"], short_exit["reason"]) == ("EXIT", "LOWER_OUTER_RISING")


def test_strong_half_channel_reversal_bypasses_ma3_outer_depth():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        101.2, 99.8, 99.7, 101.3, 100.9,
    ]
    frame.loc[frame.index[-1], ["open", "close", "low", "high", "ma3"]] = [
        99.8, 99.6, 99.5, 100.0, 100.5,
    ]

    result = TradingEngine._channel_swing_action(frame, 99.6, "LONG")

    assert (result["action"], result["side"]) == ("REVERSE", "SHORT")
    assert result["turn_high"] == pytest.approx(101.3)


def test_btc_1m_pulse_requires_atr_move_and_ma3_alignment():
    frame = pd.DataFrame({
        "close": [100.0, 100.0, 100.2, 100.5, 100.8],
        "ma3": [100.0, 100.0, 100.1, 100.3, 100.6],
        "atr": [1.0] * 5,
    })
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.8) == "LONG"

    frame["close"] = [100.8, 100.8, 100.6, 100.3, 100.0]
    frame["ma3"] = [100.8, 100.8, 100.7, 100.5, 100.2]
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.0) == "SHORT"

    frame["close"] = [100.0, 100.0, 100.1, 100.2, 100.3]
    frame["ma3"] = [100.0, 100.0, 100.1, 100.2, 100.25]
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.3) is None
    assert TradingEngine._btc_pulse_blocks_entry("SHORT", "LONG") is True
    assert TradingEngine._btc_pulse_blocks_entry("LONG", "SHORT") is True
    assert TradingEngine._btc_pulse_blocks_entry("LONG", "LONG") is False
    assert TradingEngine._btc_pulse_blocks_entry("SHORT", None) is False


def test_market_candidates_keep_only_strongest_per_direction():
    candidates = [
        {"symbol": "SOL/USDT", "side": "LONG", "score": 100, "trend_quality": 0.8},
        {"symbol": "XRP/USDT", "side": "LONG", "score": 95, "trend_quality": 1.2},
        {"symbol": "DOGE/USDT", "side": "SHORT", "score": 90, "trend_quality": 0.7},
    ]

    selected, skipped = TradingEngine._select_strongest_same_side_candidates(candidates)

    assert [item["symbol"] for item in selected] == ["XRP/USDT", "DOGE/USDT"]
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
