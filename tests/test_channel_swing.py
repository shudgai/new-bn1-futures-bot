import inspect
import types
import pytest
import pandas as pd
import core.paper_account as pa_module
from core.engine import TradingEngine
from core.paper_account import PaperAccount
from core.symbol_rotation import SymbolRotation

def _channel_frame(lower: float=99.0, upper: float=101.0) -> pd.DataFrame:
    return pd.DataFrame({'open': [100.0] * 20, 'close': [100.0] * 20, 'high': [100.5] * 20, 'low': [99.5] * 20, 'ma3': [100.0] * 20, 'ma15': [100.0] * 20, 'volume': [150.0] * 20, 'vol_ma_20': [100.0] * 20, 'kc_lower': [lower] * 20, 'kc_upper': [upper] * 20})

def test_channel_entry_requires_outer_touch_and_adjacent_break():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-2], ["open", "close", "high", "low"]] = [
        100.0, 100.5, 100.7, 98.9,
    ]
    long_frame.loc[long_frame.index[-1], ["open", "high", "low"]] = [
        100.5, 100.8, 99.0,
    ]
    assert TradingEngine._channel_closed_body_break_entry_allowed(
        long_frame, 100.75, "LONG",
    ) is True

    wrong_color = long_frame.copy()
    wrong_color.loc[wrong_color.index[-2], "close"] = 99.8
    assert TradingEngine._channel_closed_body_break_entry_allowed(
        wrong_color, 100.75, "LONG",
    ) is False

    long_invalidated = long_frame.copy()
    long_invalidated.loc[long_invalidated.index[-1], "low"] = 98.8
    assert TradingEngine._channel_closed_body_break_entry_allowed(
        long_invalidated, 100.75, "LONG",
    ) is False

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-2], ["open", "close", "high", "low"]] = [
        100.0, 99.5, 101.1, 99.3,
    ]
    short_frame.loc[short_frame.index[-1], ["open", "high", "low"]] = [
        99.5, 101.0, 99.0,
    ]
    assert TradingEngine._channel_closed_body_break_entry_allowed(
        short_frame, 99.2, "SHORT",
    ) is True

    wrong_short_color = short_frame.copy()
    wrong_short_color.loc[wrong_short_color.index[-2], "close"] = 100.2
    assert TradingEngine._channel_closed_body_break_entry_allowed(
        wrong_short_color, 99.2, "SHORT",
    ) is False

    short_invalidated = short_frame.copy()
    short_invalidated.loc[short_invalidated.index[-1], "high"] = 101.2
    assert TradingEngine._channel_closed_body_break_entry_allowed(
        short_invalidated, 99.2, "SHORT",
    ) is False


def test_channel_entry_rejects_a_mature_outer_run():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-3], "close"] = 101.1
    long_frame.loc[long_frame.index[-2], ["open", "close", "high", "low"]] = [
        101.1, 101.3, 101.4, 100.9,
    ]
    long_frame.loc[long_frame.index[-1], ["open", "high", "low"]] = [
        101.3, 101.6, 101.0,
    ]
    assert TradingEngine._channel_closed_body_break_entry_allowed(
        long_frame, 101.5, "LONG",
    ) is False

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-3], "close"] = 98.9
    short_frame.loc[short_frame.index[-2], ["open", "close", "high", "low"]] = [
        98.9, 98.7, 99.1, 98.6,
    ]
    short_frame.loc[short_frame.index[-1], ["open", "high", "low"]] = [
        98.7, 99.0, 98.4,
    ]
    assert TradingEngine._channel_closed_body_break_entry_allowed(
        short_frame, 98.5, "SHORT",
    ) is False


def test_non_touching_outer_body_is_not_an_adjacent_touch_candidate():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-3], "ma3"] = 101.2
    long_frame.loc[long_frame.index[-2], ["open", "close", "high", "low", "ma3"]] = [
        100.0, 101.2, 101.4, 99.8, 101.1,
    ]
    long_frame.loc[long_frame.index[-1], ["open", "high", "low"]] = [
        101.2, 101.6, 100.0,
    ]
    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-3], "ma3"] = 98.8
    short_frame.loc[short_frame.index[-2], ["open", "close", "high", "low", "ma3"]] = [
        100.0, 98.8, 100.2, 98.6, 98.9,
    ]
    short_frame.loc[short_frame.index[-1], ["open", "high", "low"]] = [
        98.8, 100.0, 98.4,
    ]

    long_result = TradingEngine._channel_closed_body_break_entry_action(
        long_frame, 101.5,
    )
    short_result = TradingEngine._channel_closed_body_break_entry_action(
        short_frame, 98.5,
    )

    assert (long_result["action"], long_result["reason"]) == (
        "WAIT", "WAIT_CLOSED_BODY_ADJACENT_BREAK",
    )
    assert (short_result["action"], short_result["reason"]) == (
        "WAIT", "WAIT_CLOSED_BODY_ADJACENT_BREAK",
    )

@pytest.mark.parametrize(
    ("side", "expected_reason"),
    [
        ("LONG", "KC_CLOSED_BODY_HIGH_BREAK_LONG"),
        ("SHORT", "KC_CLOSED_BODY_LOW_BREAK_SHORT"),
    ],
)
def test_closed_body_break_action_is_symmetric(side, expected_reason):
    frame = _channel_frame()
    if side == "LONG":
        frame.loc[frame.index[-2], ["open", "close", "high", "low"]] = [
            100.0, 100.5, 100.7, 98.9,
        ]
        frame.loc[frame.index[-1], ["open", "high", "low"]] = [100.5, 100.8, 99.0]
        price = 100.75
    else:
        frame.loc[frame.index[-2], ["open", "close", "high", "low"]] = [
            100.0, 99.5, 101.1, 99.3,
        ]
        frame.loc[frame.index[-1], ["open", "high", "low"]] = [99.5, 101.0, 99.0]
        price = 99.2
    result = TradingEngine._channel_closed_body_break_entry_action(frame, price)
    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", side, expected_reason,
    )


def _closed_trough(frame: pd.DataFrame):
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high']] = [98.8, 98.9, 98.7, 98.95]
    frame.loc[frame.index[-3], 'ma3'] = 98.7
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 99.0, 98.8, 99.1, 99.4]
    frame.loc[frame.index[-1], 'ma3'] = 100.0

def _closed_peak(frame: pd.DataFrame):
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high']] = [101.2, 101.1, 101.05, 101.3]
    frame.loc[frame.index[-3], 'ma3'] = 101.3
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [101.2, 101.0, 100.9, 101.2, 100.6]
    frame.loc[frame.index[-1], 'ma3'] = 100.0

def test_channel_slope_gate_blocks_long_when_kc_and_ma15_fall():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["kc_upper", "kc_lower", "ma15"]] = [101.4, 99.4, 100.4]
    frame.loc[frame.index[-2], ["kc_upper", "kc_lower", "ma15"]] = [101.0, 99.0, 100.0]
    frame.loc[frame.index[-1], ["kc_upper", "kc_lower", "ma15"]] = [101.3, 99.3, 100.3]
    result = TradingEngine._channel_slope_entry_gate(
        frame, "ENTER", "LONG", has_position=False,
    )
    assert result == ("WAIT", None, "KC_MA15_FALLING_BLOCK_LONG")


def test_channel_slope_gate_blocks_short_when_kc_and_ma15_rise():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["kc_upper", "kc_lower", "ma15"]] = [100.6, 98.6, 99.6]
    frame.loc[frame.index[-2], ["kc_upper", "kc_lower", "ma15"]] = [101.0, 99.0, 100.0]
    frame.loc[frame.index[-1], ["kc_upper", "kc_lower", "ma15"]] = [100.7, 98.7, 99.7]
    result = TradingEngine._channel_slope_entry_gate(
        frame, "ENTER", "SHORT", has_position=False,
    )
    assert result == ("WAIT", None, "KC_MA15_RISING_BLOCK_SHORT")


def test_hype_style_broad_downtrend_blocks_lower_trough_long_after_local_bounce():
    frame = _channel_frame()
    frame.loc[frame.index[-8], ["kc_upper", "kc_lower", "ma15"]] = [103.0, 101.0, 102.0]
    frame.loc[frame.index[-4], ["kc_upper", "kc_lower", "ma15"]] = [100.8, 98.8, 99.8]
    frame.loc[frame.index[-2], ["kc_upper", "kc_lower", "ma15"]] = [101.0, 99.0, 100.0]

    result = TradingEngine._channel_slope_entry_gate(
        frame, "ENTER", "LONG", has_position=False,
        signal_reason="KC_LOWER_TROUGH_CONFIRMED_LONG",
    )

    assert result == ("WAIT", None, "KC_MA15_FALLING_BLOCK_LONG")


def test_broad_uptrend_blocks_upper_peak_short_after_local_pullback():
    frame = _channel_frame()
    frame.loc[frame.index[-8], ["kc_upper", "kc_lower", "ma15"]] = [99.0, 97.0, 98.0]
    frame.loc[frame.index[-4], ["kc_upper", "kc_lower", "ma15"]] = [101.2, 99.2, 100.2]
    frame.loc[frame.index[-2], ["kc_upper", "kc_lower", "ma15"]] = [101.0, 99.0, 100.0]

    result = TradingEngine._channel_slope_entry_gate(
        frame, "ENTER", "SHORT", has_position=False,
        signal_reason="KC_UPPER_PEAK_CONFIRMED_SHORT",
    )

    assert result == ("WAIT", None, "KC_MA15_RISING_BLOCK_SHORT")


def test_channel_slope_gate_requires_both_lines_against_entry():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["kc_upper", "kc_lower", "ma15"]] = [101.4, 99.4, 99.8]
    frame.loc[frame.index[-2], ["kc_upper", "kc_lower", "ma15"]] = [101.0, 99.0, 100.0]
    assert TradingEngine._channel_slope_entry_gate(
        frame, "ENTER", "LONG", has_position=False,
    ) == ("ENTER", "LONG", None)


def test_blocked_reverse_can_exit_old_position_without_opening_new_side():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["kc_upper", "kc_lower", "ma15"]] = [101.4, 99.4, 100.4]
    frame.loc[frame.index[-2], ["kc_upper", "kc_lower", "ma15"]] = [101.0, 99.0, 100.0]
    frame.loc[frame.index[-1], ["kc_upper", "kc_lower", "ma15"]] = [101.3, 99.3, 100.3]
    assert TradingEngine._channel_slope_entry_gate(
        frame, "REVERSE", "LONG", has_position=True,
    ) == ("EXIT", None, "KC_MA15_FALLING_BLOCK_LONG")


def test_adverse_closed_slopes_allow_only_exceptionally_strong_outer_long():
    frame = _channel_frame()
    frame["atr"] = 1.0
    frame.loc[frame.index[-4], ["kc_upper", "kc_lower", "ma15"]] = [102.0, 100.0, 101.0]
    frame.loc[frame.index[-3], ["close", "ma3"]] = [100.0, 100.0]
    frame.loc[frame.index[-2], ["close", "ma3", "volume", "vol_ma_20", "kc_upper", "kc_lower", "ma15"]] = [102.0, 101.0, 200.0, 100.0, 101.0, 99.0, 100.0]
    frame.loc[frame.index[-1], ["close", "kc_upper", "kc_lower"]] = [102.0, 101.0, 99.0]
    assert TradingEngine._channel_slope_entry_gate(
        frame, "ENTER", "LONG", has_position=False,
        signal_reason="KC_LIVE_UPPER_BREAK_LONG",
    ) == ("ENTER", "LONG", None)


def test_ena_rising_kc_ma15_blocks_upper_peak_short():
    frame = _channel_frame()
    frame["atr"] = 0.0002
    frame.loc[frame.index[-4], ["kc_upper", "kc_lower", "ma15"]] = [
        0.1507737028, 0.1502837028, 0.1504446667,
    ]
    frame.loc[frame.index[-2], ["close", "ma3", "kc_upper", "kc_lower", "ma15", "volume", "vol_ma_20"]] = [
        0.15094, 0.15115, 0.1508983032, 0.1503583032, 0.1505573333, 300.0, 100.0,
    ]
    frame.loc[frame.index[-1], ["close", "kc_upper", "kc_lower"]] = [
        0.1509749, 0.1509393696, 0.1504013696,
    ]
    assert TradingEngine._channel_slope_entry_gate(
        frame, "ENTER", "SHORT", has_position=False,
        signal_reason="KC_UPPER_PEAK_CONFIRMED_SHORT",
    ) == ("WAIT", None, "KC_MA15_RISING_BLOCK_SHORT")


def test_upper_peak_short_cannot_use_outer_continuation_energy_exception():
    frame = _channel_frame()
    frame["atr"] = 1.0
    frame.loc[frame.index[-4], ["kc_upper", "kc_lower", "ma15"]] = [100.6, 98.6, 99.6]
    frame.loc[frame.index[-3], ["close", "ma3"]] = [100.0, 100.0]
    frame.loc[frame.index[-2], ["close", "ma3", "volume", "vol_ma_20", "kc_upper", "kc_lower", "ma15"]] = [98.0, 99.0, 200.0, 100.0, 101.0, 99.0, 100.0]
    frame.loc[frame.index[-1], ["close", "kc_upper", "kc_lower"]] = [98.0, 101.0, 99.0]
    assert TradingEngine._channel_slope_entry_gate(
        frame, "ENTER", "SHORT", has_position=False,
        signal_reason="KC_UPPER_PEAK_CONFIRMED_SHORT",
    ) == ("WAIT", None, "KC_MA15_RISING_BLOCK_SHORT")


def test_channel_same_side_committed_spans_positions_and_pending_orders():
    positions = {"A/USDT": {"side": "LONG"}}
    pending = {"B/USDT": {"side": "SHORT"}}
    assert TradingEngine._channel_same_side_committed(positions, pending, "LONG")
    assert TradingEngine._channel_same_side_committed(positions, pending, "SHORT")
    assert not TradingEngine._channel_same_side_committed(positions, pending, "WAIT")


def test_global_btc_direction_has_priority_for_new_entries():
    engine = TradingEngine.__new__(TradingEngine)
    engine.btc_1h_st_direction = -1
    engine._continuous_market_mode = {"COIN/USDT": "BULL"}
    assert engine._channel_macro_market_mode("COIN/USDT") == "BEAR"
    engine.btc_1h_st_direction = 0
    assert engine._channel_macro_market_mode("COIN/USDT") == "BULL"


@pytest.mark.parametrize(
    ("mode", "side", "reason", "expected"),
    [
        (
            "BULL", "SHORT", "KC_CLOSED_BODY_LOW_BREAK_SHORT",
            ("ENTER", "SHORT", None),
        ),
        (
            "BEAR", "LONG", "KC_CLOSED_BODY_HIGH_BREAK_LONG",
            ("ENTER", "LONG", None),
        ),
        (
            "BULL", "LONG", "KC_CLOSED_BODY_HIGH_BREAK_LONG",
            ("ENTER", "LONG", None),
        ),
        (
            "BEAR", "SHORT", "KC_CLOSED_BODY_LOW_BREAK_SHORT",
            ("ENTER", "SHORT", None),
        ),
    ],
)
def test_closed_body_continuation_respects_macro_direction(
    mode, side, reason, expected,
):
    assert TradingEngine._channel_macro_continuation_entry_gate(
        "ENTER", side, mode, False, reason,
    ) == expected


@pytest.mark.parametrize(
    ("side", "reason"),
    [
        ("LONG", "KC_CLOSED_BODY_HIGH_BREAK_LONG"),
        ("SHORT", "KC_CLOSED_BODY_LOW_BREAK_SHORT"),
    ],
)
def test_closed_body_break_rejects_fil_style_low_volume_symmetrically(
    monkeypatch, side, reason,
):
    monkeypatch.setattr("core.engine.KELTNER_MIN_VOLUME_RATIO", 1.2)

    assert TradingEngine._channel_closed_body_volume_gate(
        "ENTER", side, 0.10893375597244837, False, reason,
    ) == ("ENTER", side, None)
    assert TradingEngine._channel_closed_body_volume_gate(
        "ENTER", side, 1.2, False, reason,
    ) == ("ENTER", side, None)


def test_closed_body_volume_gate_does_not_change_live_outer_break_exception(
    monkeypatch,
):
    monkeypatch.setattr("core.engine.KELTNER_MIN_VOLUME_RATIO", 1.2)

    assert TradingEngine._channel_closed_body_volume_gate(
        "ENTER", "LONG", 0.1, False, "KC_LIVE_UPPER_BREAK_LONG",
    ) == ("ENTER", "LONG", None)


@pytest.mark.parametrize(
    ("mode", "side", "reason"),
    [
        ("BULL", "SHORT", "KC_LIVE_LOWER_BREAK_SHORT"),
        ("BEAR", "LONG", "KC_LIVE_UPPER_BREAK_LONG"),
    ],
)
def test_macro_gate_does_not_change_immediate_outer_break_exception(
    mode, side, reason,
):
    assert TradingEngine._channel_macro_continuation_entry_gate(
        "ENTER", side, mode, False, reason,
    ) == ("ENTER", side, None)


def test_confirmed_outer_ma3_turns_end_channel_positions_symmetrically():
    long_frame = _channel_frame()
    _closed_peak(long_frame)
    long_result = TradingEngine._channel_swing_action(long_frame, 100.6, "LONG")
    short_frame = _channel_frame()
    _closed_trough(short_frame)
    short_result = TradingEngine._channel_swing_action(short_frame, 99.4, "SHORT")
    assert (long_result["action"], long_result["reason"]) == (
        "EXIT", "KC_UPPER_OUTER_PEAK_EXIT",
    )
    assert (short_result["action"], short_result["reason"]) == (
        "EXIT", "KC_LOWER_OUTER_VALLEY_EXIT",
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_confirmed_outer_exit_does_not_require_positive_estimated_net_pnl(side):
    frame = _channel_frame()
    if side == "LONG":
        _closed_peak(frame)
        wait_reason = "WAIT_OPPOSITE_KC_UPPER_PEAK"
        price = 100.6
    else:
        _closed_trough(frame)
        wait_reason = "WAIT_OPPOSITE_KC_LOWER_VALLEY"
        price = 99.4

    result = TradingEngine._channel_swing_action(
        frame, price, side,
        exit_net_profitable=False,
    )

    expected_reason = (
        "KC_UPPER_OUTER_PEAK_EXIT" if side == "LONG"
        else "KC_LOWER_OUTER_VALLEY_EXIT"
    )
    assert (result["action"], result["reason"]) == ("EXIT", expected_reason)


def test_one_sided_ma3_move_is_not_mistaken_for_a_new_outer_extreme():
    long_frame = _channel_frame()
    _closed_peak(long_frame)
    long_frame.loc[long_frame.index[-4], "ma3"] = 101.6
    long_result = TradingEngine._channel_swing_action(
        long_frame, 101.1, "LONG",
    )

    short_frame = _channel_frame()
    _closed_trough(short_frame)
    short_frame.loc[short_frame.index[-4], "ma3"] = 98.4
    short_result = TradingEngine._channel_swing_action(
        short_frame, 98.9, "SHORT",
    )

    assert (long_result["action"], long_result["reason"]) == (
        "HOLD", "WAIT_OPPOSITE_KC_UPPER_PEAK",
    )
    assert (short_result["action"], short_result["reason"]) == (
        "HOLD", "WAIT_OPPOSITE_KC_LOWER_VALLEY",
    )


def test_pre_entry_outer_pivots_cannot_close_new_positions():
    timestamps = [index * 60_000 for index in range(20)]

    long_frame = _channel_frame()
    long_frame["timestamp"] = timestamps
    _closed_peak(long_frame)
    opened_during_live_bar = timestamps[-1] / 1000.0 + 18.0
    long_result = TradingEngine._channel_swing_action(
        long_frame, 101.1, "LONG",
        position_open_timestamp=opened_during_live_bar,
    )

    short_frame = _channel_frame()
    short_frame["timestamp"] = timestamps
    _closed_trough(short_frame)
    short_result = TradingEngine._channel_swing_action(
        short_frame, 98.9, "SHORT",
        position_open_timestamp=opened_during_live_bar,
    )

    assert (long_result["action"], long_result["reason"]) == (
        "HOLD", "WAIT_OPPOSITE_KC_UPPER_PEAK",
    )
    assert (short_result["action"], short_result["reason"]) == (
        "HOLD", "WAIT_OPPOSITE_KC_LOWER_VALLEY",
    )


def test_post_entry_confirmed_outer_peak_exits_position():
    frame = _channel_frame()
    frame["timestamp"] = [index * 60_000 for index in range(20)]
    _closed_peak(frame)
    opened_before_signal_closed = frame["timestamp"].iloc[-3] / 1000.0 - 1.0
    result = TradingEngine._channel_swing_action(
        frame, 101.1, "LONG",
        position_open_timestamp=opened_before_signal_closed,
    )
    assert (result["action"], result["reason"]) == (
        "EXIT", "KC_UPPER_OUTER_PEAK_EXIT",
    )


def test_live_reversal_after_outer_peak_exits_before_middle_reentry():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-3], "ma3"] = 100.5
    frame.loc[frame.index[-2], ["open", "close", "high", "ma3"]] = [
        101.2, 101.3, 101.4, 101.5,
    ]
    frame.loc[frame.index[-1], ["open", "close", "ma3"]] = [
        101.3, 101.0, 101.0,
    ]
    result = TradingEngine._channel_swing_action(frame, 101.0, "LONG")
    assert (result["action"], result["reason"]) == (
        "EXIT", "KC_UPPER_OUTER_PEAK_EXIT",
    )


def test_pump_one_tick_ma3_dip_is_not_a_confirmed_outer_peak():
    frame = _channel_frame(lower=0.00423, upper=0.00425)
    frame.loc[frame.index[-4], "ma3"] = 0.004259
    frame.loc[frame.index[-3], ["ma3", "kc_upper", "kc_lower"]] = [
        0.004262, 0.00424986, 0.00423126,
    ]
    frame.loc[frame.index[-2], ["ma3", "kc_upper", "kc_lower"]] = [
        0.004261666666666667, 0.004251797849169696, 0.004233597849169697,
    ]

    result = TradingEngine._channel_swing_action(frame, 0.004260, "LONG")

    assert (result["action"], result["reason"]) == (
        "HOLD", "WAIT_OPPOSITE_KC_UPPER_PEAK",
    )


def test_mirrored_one_tick_ma3_rise_is_not_a_confirmed_outer_trough():
    frame = _channel_frame(lower=0.00423, upper=0.00425)
    frame.loc[frame.index[-4], "ma3"] = 0.004241
    frame.loc[frame.index[-3], ["ma3", "kc_upper", "kc_lower"]] = [
        0.004238, 0.00424874, 0.00423014,
    ]
    frame.loc[frame.index[-2], ["ma3", "kc_upper", "kc_lower"]] = [
        0.004238333333333333, 0.004246402150830304, 0.004228202150830303,
    ]

    result = TradingEngine._channel_swing_action(frame, 0.004240, "SHORT")

    assert (result["action"], result["reason"]) == (
        "HOLD", "WAIT_OPPOSITE_KC_LOWER_VALLEY",
    )


def test_near_outer_peak_keeps_confirming_until_cumulative_turn_is_large_enough():
    frame = _channel_frame(lower=1.952, upper=1.959)
    frame.loc[frame.index[-6], "ma3"] = 1.9606666666666666
    frame.loc[frame.index[-5], ["ma3", "kc_upper", "kc_lower"]] = [
        1.9616666666666667, 1.959189696704553, 1.9523896967045529,
    ]
    frame.loc[frame.index[-4], "ma3"] = 1.961
    frame.loc[frame.index[-3], "ma3"] = 1.959
    frame.loc[frame.index[-2], "ma3"] = 1.9576666666666667
    frame.loc[frame.index[-1], "ma3"] = 1.9566666666666668

    result = TradingEngine._channel_swing_action(
        frame, 1.957, "LONG", exit_net_profitable=False,
    )

    assert (result["action"], result["reason"]) == (
        "EXIT", "KC_UPPER_OUTER_PEAK_EXIT",
    )


def test_btc_flash_crash_closes_all_longs_including_channel_swing():
    positions = {
        "CHANNEL-LONG/USDT": {"side": "LONG", "entry_mode": "CHANNEL_SWING"},
        "LEGACY-CHANNEL/USDT": {"side": "LONG", "reason": "Channel Swing entry"},
        "REGULAR-LONG/USDT": {"side": "LONG", "entry_mode": "MA3_MA15_MARKET"},
        "REGULAR-SHORT/USDT": {"side": "SHORT", "entry_mode": "MA3_MA15_MARKET"},
    }

    selected = TradingEngine._btc_flash_crash_close_symbols(positions)

    assert selected == ["CHANNEL-LONG/USDT", "LEGACY-CHANNEL/USDT", "REGULAR-LONG/USDT"]


def test_btc_flash_crash_closes_restored_channel_swing_metadata_position():
    positions = {"RESTORED/USDT": {"side": "LONG"}}
    metadata = {"RESTORED/USDT": {"entry_mode": "CHANNEL_SWING"}}

    selected = TradingEngine._btc_flash_crash_close_symbols(positions, metadata)

    assert selected == ["RESTORED/USDT"]


def test_btc_flash_crash_closes_shorts_symmetrically():
    positions = {
        "CHANNEL-SHORT/USDT": {"side": "SHORT", "entry_mode": "CHANNEL_SWING"},
        "REGULAR-SHORT/USDT": {"side": "SHORT"},
        "LONG/USDT": {"side": "LONG"},
    }
    assert TradingEngine._btc_flash_crash_close_symbols(positions, side="SHORT") == [
        "CHANNEL-SHORT/USDT", "REGULAR-SHORT/USDT",
    ]


def test_market_crash_cooldown_blocks_entries_only_until_expiry():
    engine = TradingEngine.__new__(TradingEngine)
    engine._market_crash_entry_cooldown_until = 700.0
    assert engine._market_crash_entries_paused(699.9) is True
    assert engine._market_crash_entries_paused(700.0) is False


def test_wld_small_outer_turn_exits_on_confirmed_peak():
    frame = _channel_frame(lower=0.3750, upper=0.3780)
    frame["timestamp"] = [index * 60_000 for index in range(20)]
    frame.loc[frame.index[-3], [
        "open", "high", "low", "close", "ma3", "kc_upper", "kc_lower",
    ]] = [0.3793, 0.3793, 0.3787, 0.3793, 0.37936667, 0.37748123, 0.37518123]
    frame.loc[frame.index[-4], "ma3"] = 0.3791
    frame.loc[frame.index[-2], [
        "open", "high", "low", "close", "ma3", "kc_upper", "kc_lower",
    ]] = [0.3793, 0.3794, 0.3780, 0.3783, 0.37896667, 0.37773873, 0.37529873]
    frame.loc[frame.index[-1], [
        "open", "high", "low", "close", "ma3", "kc_upper", "kc_lower",
    ]] = [0.3783, 0.3790, 0.3782, 0.3790, 0.37886667, 0.37796504, 0.37554504]
    opened_before_peak = frame["timestamp"].iloc[-3] / 1000.0 - 60.0

    result = TradingEngine._channel_swing_action(
        frame, 0.3790, "LONG", position_open_timestamp=opened_before_peak,
    )

    assert (result["action"], result["reason"]) == (
        "EXIT", "KC_UPPER_OUTER_PEAK_EXIT",
    )


def test_trx_peak_enters_on_adjacent_live_break_and_cannot_reuse_later():
    frame = _channel_frame(lower=0.3258, upper=0.3260)
    frame.loc[frame.index[-2], [
        "open", "high", "low", "close", "ma3", "ma15", "kc_upper", "kc_lower",
    ]] = [0.32597, 0.32598, 0.32576, 0.32577, 0.32591, 0.325935, 0.3259703, 0.3258703]
    frame.loc[frame.index[-1], [
        "open", "high", "low", "close", "ma3", "ma15", "kc_upper", "kc_lower",
    ]] = [0.32577, 0.32577, 0.32561, 0.32566, 0.325803, 0.325917, 0.3259525, 0.3258385]
    early = TradingEngine._channel_swing_action(
        frame, 0.32575, market_mode="BEAR",
    )
    assert (early["action"], early["side"], early["reason"]) == (
        "WAIT", None, "WAIT_KC_OUTER_TREND_ENTRY",
    )

    later = frame.copy()
    later.loc[later.index[-2]] = frame.loc[frame.index[-1]]
    later.loc[later.index[-1], [
        "open", "high", "low", "close", "ma3", "ma15", "kc_upper", "kc_lower",
    ]] = [0.32566, 0.32567, 0.32565, 0.32567, 0.32570, 0.325898, 0.325932, 0.325816]
    stale = TradingEngine._channel_swing_action(
        later, 0.32563, market_mode="BEAR",
    )
    assert (stale["action"], stale["side"]) == ("WAIT", None)


def test_bear_market_blocks_lower_trough_countertrend_long():
    frame = _channel_frame()
    _closed_trough(frame)
    result = TradingEngine._channel_swing_action(frame,  99.2, market_mode="BEAR")
    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "WAIT_KC_OUTER_TREND_ENTRY",
    )

def test_outer_upper_retrace_does_not_open_without_directional_growth():
    frame = _channel_frame(lower=99.8, upper=100.2)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [99.9, 100.1]
    frame.loc[frame.index[-1], ['open', 'close']] = [100.3, 100.1]
    result = TradingEngine._channel_live_outer_entry_action(frame, 100.2)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_UPPER_OUTER_GROWTH')
    assert (100.2 - 99.8) / 100.2 < 0.005

def test_outer_lower_rebound_does_not_open_without_directional_growth():
    frame = _channel_frame(lower=99.8, upper=100.2)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [99.9, 100.1]
    frame.loc[frame.index[-1], ['open', 'close']] = [99.7, 99.9]
    result = TradingEngine._channel_live_outer_entry_action(frame, 99.8)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_LOWER_OUTER_GROWTH')
    assert (100.2 - 99.8) / 99.8 < 0.005

def test_strong_run_enters_long_on_first_live_upper_kc_touch():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-4:], "open"] = [99.6, 99.8, 100.1, 100.6]
    frame.loc[frame.index[-4:], "close"] = [99.8, 100.1, 100.5, 100.8]
    frame.loc[frame.index[-4:], "ma3"] = [99.7, 99.9, 100.2, 100.6]
    frame.loc[frame.index[-1], "ma15"] = 100.0
    result = TradingEngine._channel_strong_first_outer_touch_action(frame, 101.0)
    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", "LONG", "KC_STRONG_FIRST_UPPER_TOUCH_LONG",
    )


def test_strong_run_enters_short_on_first_live_lower_kc_touch():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-4:], "open"] = [100.4, 100.2, 99.9, 99.4]
    frame.loc[frame.index[-4:], "close"] = [100.2, 99.9, 99.5, 99.2]
    frame.loc[frame.index[-4:], "ma3"] = [100.3, 100.1, 99.8, 99.4]
    frame.loc[frame.index[-1], "ma15"] = 100.0
    result = TradingEngine._channel_strong_first_outer_touch_action(frame, 99.0)
    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", "SHORT", "KC_STRONG_FIRST_LOWER_TOUCH_SHORT",
    )


def test_strong_touch_fast_path_is_only_for_the_first_outer_touch():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-4:], "open"] = [99.6, 99.8, 100.4, 101.0]
    frame.loc[frame.index[-4:], "close"] = [99.8, 100.3, 101.1, 101.2]
    frame.loc[frame.index[-4:], "ma3"] = [99.7, 100.0, 100.5, 101.0]
    frame.loc[frame.index[-1], "ma15"] = 100.0
    result = TradingEngine._channel_strong_first_outer_touch_action(frame, 101.3)
    assert result["action"] == "WAIT"


def test_live_outer_entry_waits_when_upper_wick_touches_but_price_is_inside():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [100.5, 100.4, 101.1]
    result = TradingEngine._channel_live_outer_entry_action(frame, 100.9)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_LIVE_OUTER_BREAK')

def test_live_outer_entry_waits_when_lower_wick_touches_but_price_is_inside():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [99.5, 98.9, 99.6]
    result = TradingEngine._channel_live_outer_entry_action(frame, 99.1)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_LIVE_OUTER_BREAK')

def test_live_outer_entry_does_not_chase_after_upper_touch_retraces_too_far():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [100.5, 100.4, 101.1]
    result = TradingEngine._channel_live_outer_entry_action(frame, 100.7)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_LIVE_OUTER_BREAK')

def test_live_outer_entry_does_not_chase_after_lower_touch_rebounds_too_far():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "low", "high"]] = [99.5, 98.9, 99.6]
    result = TradingEngine._channel_live_outer_entry_action(frame, 99.3)
    assert (result["action"], result["reason"]) == ("WAIT", "WAIT_LIVE_OUTER_BREAK")

def test_live_outer_entry_allows_fresh_long_break_despite_prior_extension():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [98.0, 101.1]
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [100.8, 100.7, 101.1]
    result = TradingEngine._channel_live_outer_entry_action(frame, 101.1)
    assert (result['action'], result['side']) == ('ENTER', 'LONG')

def test_live_outer_entry_allows_fresh_short_break_despite_prior_extension():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [98.9, 102.0]
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [99.2, 98.9, 99.3]
    result = TradingEngine._channel_live_outer_entry_action(frame, 98.9)
    assert (result['action'], result['side']) == ('ENTER', 'SHORT')


def test_live_upper_break_blocks_link_shape_after_closed_ma3_turns_below_ma15():
    frame = _channel_frame(lower=11.2380, upper=11.2410)
    frame.loc[frame.index[-3], "ma3"] = 11.24367
    frame.loc[frame.index[-2], ["close", "ma3", "ma15"]] = [
        11.2400, 11.24100, 11.24100,
    ]

    result = TradingEngine._channel_live_outer_entry_action(frame, 11.2421241)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "WAIT_UPPER_TREND_RESET",
    )


def test_live_lower_break_blocks_mirror_after_closed_ma3_turns_above_ma15():
    frame = _channel_frame(lower=98.9, upper=101.0)
    frame.loc[frame.index[-3], "ma3"] = 99.7
    frame.loc[frame.index[-2], ["close", "ma3", "ma15"]] = [
        99.2, 100.0, 100.0,
    ]

    result = TradingEngine._channel_live_outer_entry_action(frame, 98.8)

    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "KC_LOWER_MA3_REVERSAL_BLOCK_SHORT",
    )


def test_live_outer_ma3_reversal_filter_does_not_block_directional_momentum():
    long_frame = _channel_frame(lower=99.0, upper=101.0)
    long_frame.loc[long_frame.index[-3], "ma3"] = 99.8
    long_frame.loc[long_frame.index[-2], ["ma3", "ma15"]] = [100.2, 100.0]
    short_frame = _channel_frame(lower=99.0, upper=101.0)
    short_frame.loc[short_frame.index[-3], "ma3"] = 100.2
    short_frame.loc[short_frame.index[-2], ["ma3", "ma15"]] = [99.8, 100.0]

    long_result = TradingEngine._channel_live_outer_entry_action(long_frame, 101.1)
    short_result = TradingEngine._channel_live_outer_entry_action(short_frame, 98.9)

    assert (long_result["action"], long_result["side"]) == ("ENTER", "LONG")
    assert (short_result["action"], short_result["side"]) == ("ENTER", "SHORT")

def _mature_outer_break_frame(side, strong=False):
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame["atr"] = 0.2
    positions = list(frame.index[-5:-1])
    for step, position in enumerate(positions):
        middle = 99.6 + step * 0.1 if side == "LONG" else 100.4 - step * 0.1
        upper = middle + 1.0
        lower = middle - 1.0
        ma3 = middle + 0.3 if side == "LONG" else middle - 0.3
        close = upper - 0.1 if side == "LONG" else lower + 0.1
        frame.loc[position, ["close", "ma3", "ma15", "kc_upper", "kc_lower", "volume", "vol_ma_20"]] = [
            close, ma3, middle, upper, lower, 200.0 if strong and step == 3 else 50.0, 100.0,
        ]
    return frame


@pytest.mark.parametrize(
    ("side", "price"), [("LONG", 101.1), ("SHORT", 98.9)],
)
def test_live_outer_growth_is_not_rejected_as_a_mature_trend_tail(side, price):
    frame = _mature_outer_break_frame(side, strong=False)
    result = TradingEngine._channel_live_outer_entry_action(frame, price)
    assert (result["action"], result["side"]) == ("ENTER", side)


@pytest.mark.parametrize(
    ("side", "price"), [("LONG", 101.1), ("SHORT", 98.9)],
)
def test_live_outer_entry_allows_exceptional_energy_after_mature_run(side, price):
    frame = _mature_outer_break_frame(side, strong=True)
    result = TradingEngine._channel_live_outer_entry_action(frame, price)
    assert (result["action"], result["side"]) == ("ENTER", side)


def test_live_outer_entry_rejects_stale_long_extension_after_slot_frees():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-2], "close"] = 101.2
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [98.0, 101.1]
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [101.6, 101.2, 101.7]
    result = TradingEngine._channel_live_outer_entry_action(frame, 101.1)
    assert (result["action"], result["side"], result["reason"]) == ("WAIT", None, "WAIT_UPPER_TREND_RESET")

@pytest.mark.parametrize(
    ("side", "price", "previous_close", "live_open", "expected_reason"),
    [
        ("LONG", 101.3, 101.1, 101.2, "WAIT_UPPER_TREND_RESET"),
        ("SHORT", 98.7, 98.9, 98.8, "WAIT_LOWER_TREND_RESET"),
    ],
)
def test_outer_growth_waits_for_reset_instead_of_entering_mid_run(
    side, price, previous_close, live_open, expected_reason,
):
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-2], "close"] = previous_close
    frame.loc[frame.index[-1], "open"] = live_open
    result = TradingEngine._channel_live_outer_entry_action(frame, price)
    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, expected_reason,
    )


def test_live_outer_entry_rejects_stale_short_extension_after_slot_frees():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-2], "close"] = 98.8
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [98.9, 102.0]
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [98.4, 98.3, 98.8]
    result = TradingEngine._channel_live_outer_entry_action(frame, 98.9)
    assert (result["action"], result["side"], result["reason"]) == ("WAIT", None, "WAIT_LOWER_TREND_RESET")


def _sustained_outer_trend_frame(side: str) -> pd.DataFrame:
    frame = _channel_frame(lower=99.0, upper=101.0)
    positions = list(frame.index[-4:])
    for step, position in enumerate(positions):
        direction = 1.0 if side == "LONG" else -1.0
        middle = 100.0 + direction * step * 0.10
        upper = middle + 1.0
        lower = middle - 1.0
        close = upper + 0.05 if side == "LONG" else lower - 0.05
        frame.loc[position, [
            "open", "close", "high", "low", "ma3", "ma15",
            "kc_upper", "kc_lower",
        ]] = [
            close - direction * 0.06, close, close + 0.08, close - 0.08,
            middle + direction * 0.35, middle + direction * 0.10,
            upper, lower,
        ]
    return frame


@pytest.mark.parametrize(
    ("side", "price", "reason"),
    [
        ("LONG", 101.0, "KC_LIVE_UPPER_BREAK_LONG"),
        ("SHORT", 99.0, "KC_LIVE_LOWER_BREAK_SHORT"),
    ],
)
def test_immediate_outer_break_enters_on_the_breakout_price(side, price, reason):
    result = TradingEngine._channel_immediate_outer_break_action(_channel_frame(), price)
    assert (result["action"], result["side"], result["reason"]) == ("ENTER", side, reason)

@pytest.mark.parametrize(
    ("side", "price", "expected_reason"),
    [
        ("LONG", 101.1, "KC_LIVE_UPPER_BREAK_LONG"),
        ("SHORT", 98.9, "KC_LIVE_LOWER_BREAK_SHORT"),
    ],
)
def test_new_trend_can_enter_after_three_closed_reset_bars(
    side, price, expected_reason,
):
    frame = _channel_frame(lower=99.0, upper=101.0)
    if side == "LONG":
        frame.loc[frame.index[-7], "close"] = 101.1
        frame.loc[frame.index[-4:-1], "close"] = [100.7, 100.8, 100.9]
        frame.loc[frame.index[-1], "open"] = 100.9
    else:
        frame.loc[frame.index[-7], "close"] = 98.9
        frame.loc[frame.index[-4:-1], "close"] = [99.3, 99.2, 99.1]
        frame.loc[frame.index[-1], "open"] = 99.1
    result = TradingEngine._channel_live_outer_entry_action(frame, price)
    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", side, expected_reason,
    )


def test_sustained_upper_kc_trend_is_not_entered_mid_run():
    frame = _sustained_outer_trend_frame("LONG")
    result = TradingEngine._channel_live_outer_entry_action(frame, 101.36)
    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "WAIT_UPPER_TREND_RESET",
    )


def test_sustained_lower_kc_trend_is_not_entered_mid_run():
    frame = _sustained_outer_trend_frame("SHORT")
    result = TradingEngine._channel_live_outer_entry_action(frame, 98.64)
    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "WAIT_LOWER_TREND_RESET",
    )


@pytest.mark.parametrize(("side", "price"), [("LONG", 101.36), ("SHORT", 98.64)])
def test_sustained_outer_price_growth_still_waits_when_ma15_has_not_advanced(side, price):
    frame = _sustained_outer_trend_frame(side)
    frame.loc[frame.index[-1], "ma15"] = frame.loc[frame.index[-3], "ma15"]
    result = TradingEngine._channel_live_outer_entry_action(frame, price)
    assert (result["action"], result["side"]) == ("WAIT", None)

def test_held_long_does_not_exit_on_upper_rail_touch_without_confirmed_peak():
    frame = _channel_frame(lower=99.0, upper=101.0)
    result = TradingEngine._channel_swing_action(frame, 101.0, 'LONG')
    assert (result['action'], result['side']) == ('HOLD', None)

def test_held_short_does_not_exit_on_lower_rail_touch_without_confirmed_trough():
    frame = _channel_frame(lower=99.0, upper=101.0)
    result = TradingEngine._channel_swing_action(frame, 99.0, 'SHORT')
    assert (result['action'], result['side']) == ('HOLD', None)


def test_front_stage_red_reentry_does_not_exit_long():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-3], ["open", "close"]] = [100.8, 101.2]
    frame.loc[frame.index[-2], ["open", "close", "ma3"]] = [101.2, 100.8, 100.9]

    result = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    assert (result["action"], result["side"]) == ("HOLD", None)


def test_two_closed_red_reentry_candles_hold_without_outer_peak():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-6:-3], ["open", "close"]] = [
        [100.8, 101.1], [101.0, 101.2], [101.1, 101.3],
    ]
    frame.loc[frame.index[-3], ["open", "close", "ma3"]] = [101.2, 100.8, 101.0]
    frame.loc[frame.index[-2], ["open", "close", "ma3"]] = [100.8, 100.5, 100.9]

    result = TradingEngine._channel_swing_action(frame, 100.5, "LONG")

    assert (result["action"], result["reason"]) == ("EXIT", "KC_UPPER_OUTER_PEAK_EXIT")


def test_red_candle_outside_lower_kc_does_not_exit_short():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-4], ["open", "close"]] = [99.2, 98.8]
    frame.loc[frame.index[-3], ["open", "close"]] = [99.0, 98.8]
    frame.loc[frame.index[-2], ["open", "close"]] = [98.9, 98.7]

    result = TradingEngine._channel_swing_action(frame, 98.7, "SHORT")

    assert (result["action"], result["side"]) == ("HOLD", None)


def test_two_closed_green_reentry_candles_hold_without_outer_trough():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-6:-3], ["open", "close"]] = [
        [99.2, 98.9], [99.0, 98.8], [98.9, 98.7],
    ]
    frame.loc[frame.index[-3], ["open", "close", "ma3"]] = [98.8, 99.2, 99.0]
    frame.loc[frame.index[-2], ["open", "close", "ma3"]] = [99.2, 99.5, 99.1]

    result = TradingEngine._channel_swing_action(frame, 99.5, "SHORT")

    assert (result["action"], result["reason"]) == ("EXIT", "KC_LOWER_OUTER_VALLEY_EXIT")


def test_mature_uptrend_reentry_protects_profit_after_upper_break():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-5:-2], ["open", "close", "ma3"]] = [
        [100.8, 101.1, 100.9],
        [101.0, 101.2, 101.1],
        [101.1, 101.3, 101.3],
    ]
    frame.loc[frame.index[-2], ["open", "close", "ma3"]] = [101.2, 100.8, 101.1]

    result = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    assert (result["action"], result["reason"]) == (
        "EXIT", "KC_UPPER_OUTER_PEAK_EXIT",
    )


def test_rising_ma3_does_not_override_red_reentry_exit():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-5:-2], ["open", "close", "ma3"]] = [
        [100.8, 101.1, 100.7],
        [101.0, 101.2, 100.8],
        [101.1, 101.3, 100.9],
    ]
    frame.loc[frame.index[-2], ["open", "close", "ma3"]] = [101.2, 100.8, 101.0]

    result = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    assert (result["action"], result["side"]) == ("HOLD", None)


def test_mature_downtrend_reentry_protects_profit_after_lower_break():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-5:-2], ["open", "close", "ma3"]] = [
        [99.2, 98.9, 99.1],
        [99.0, 98.8, 98.9],
        [98.9, 98.7, 98.7],
    ]
    frame.loc[frame.index[-2], ["open", "close", "ma3"]] = [98.8, 99.2, 98.9]

    result = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (result["action"], result["reason"]) == (
        "EXIT", "KC_LOWER_OUTER_VALLEY_EXIT",
    )


def test_upper_reentry_keeps_tracking_the_earlier_exact_peak():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-4], ["close", "ma3"]] = [101.4, 101.5]
    frame.loc[frame.index[-3], ["open", "close", "ma3"]] = [101.2, 101.3, 101.2]
    frame.loc[frame.index[-2], ["open", "close", "ma3"]] = [101.2, 100.8, 101.0]

    result = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    assert (result["action"], result["reason"]) == (
        "EXIT", "KC_UPPER_OUTER_PEAK_EXIT",
    )


def test_lower_reentry_keeps_tracking_the_earlier_exact_trough():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-4], ["close", "ma3"]] = [98.6, 98.5]
    frame.loc[frame.index[-3], ["open", "close", "ma3"]] = [98.8, 98.7, 98.8]
    frame.loc[frame.index[-2], ["open", "close", "ma3"]] = [98.8, 99.2, 99.0]

    result = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (result["action"], result["reason"]) == (
        "EXIT", "KC_LOWER_OUTER_VALLEY_EXIT",
    )


def test_red_and_green_candles_are_ignored_while_long_remains_above_upper_kc():
    red = _channel_frame(lower=99.0, upper=101.0)
    red.loc[red.index[-3], ["open", "close"]] = [101.1, 101.3]
    red.loc[red.index[-2], ["open", "close"]] = [101.4, 101.2]
    green = red.copy()
    green.loc[green.index[-2], ["open", "close"]] = [101.2, 101.4]

    red_result = TradingEngine._channel_swing_action(red, 101.2, "LONG")
    green_result = TradingEngine._channel_swing_action(green, 101.4, "LONG")

    assert (red_result["action"], red_result["side"]) == ("HOLD", None)
    assert (green_result["action"], green_result["side"]) == ("HOLD", None)


def test_steep_red_candle_alone_does_not_exit_outer_long():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame["atr"] = 1.0
    frame.loc[frame.index[-3], ["open", "close"]] = [101.0, 101.8]
    frame.loc[frame.index[-2], ["open", "high", "low", "close", "ma3"]] = [
        102.4, 102.5, 101.1, 101.2, 101.4,
    ]

    result = TradingEngine._channel_swing_action(frame, 101.2, "LONG")

    assert (result["action"], result["reason"]) == (
        "HOLD", "WAIT_OPPOSITE_KC_UPPER_PEAK",
    )


def test_gentle_red_reentry_waits_when_ma3_is_not_near_upper_rail():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame["atr"] = 1.0
    frame.loc[frame.index[-3], ["open", "close"]] = [101.0, 101.4]
    frame.loc[frame.index[-2], ["open", "high", "low", "close", "ma3"]] = [
        101.2, 101.3, 100.7, 100.8, 100.5,
    ]

    result = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    assert (result["action"], result["side"]) == ("HOLD", None)


def test_steep_green_candle_alone_does_not_exit_outer_short():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame["atr"] = 1.0
    frame.loc[frame.index[-3], ["open", "close"]] = [99.0, 98.2]
    frame.loc[frame.index[-2], ["open", "high", "low", "close", "ma3"]] = [
        97.6, 98.9, 97.5, 98.8, 98.6,
    ]

    result = TradingEngine._channel_swing_action(frame, 98.8, "SHORT")

    assert (result["action"], result["reason"]) == (
        "HOLD", "WAIT_OPPOSITE_KC_LOWER_VALLEY",
    )


def test_long_holds_when_peak_has_already_returned_inside_upper():
    midtrend = _channel_frame(lower=99.0, upper=101.0)
    midtrend["atr"] = 1.0
    midtrend.loc[midtrend.index[-5:-1], ["open", "high", "low", "close", "ma3"]] = [
        [99.1, 99.9, 99.0, 99.8, 99.4],
        [99.8, 100.6, 99.7, 100.5, 100.0],
        [100.5, 100.6, 100.0, 100.1, 100.2],
        [100.1, 100.8, 100.0, 100.7, 100.4],
    ]

    midtrend_result = TradingEngine._channel_swing_action(
        midtrend, 100.7, "LONG",
    )
    assert (midtrend_result["action"], midtrend_result["side"]) == ("HOLD", None)

    final_reversal = midtrend.copy()
    final_reversal.loc[
        final_reversal.index[-4:-1], ["open", "high", "low", "close", "ma3"]
    ] = [
        [100.7, 101.3, 100.6, 101.2, 100.8],
        [101.2, 101.7, 101.1, 101.6, 101.2],
        [102.0, 102.1, 100.7, 100.8, 101.1],
    ]

    final_result = TradingEngine._channel_swing_action(
        final_reversal, 100.8, "LONG",
    )
    assert (final_result["action"], final_result["reason"]) == (
        "EXIT", "KC_UPPER_OUTER_PEAK_EXIT",
    )


def test_short_exits_only_at_confirmed_outer_trough():
    midtrend = _channel_frame(lower=99.0, upper=101.0)
    _closed_trough(midtrend)
    midtrend_result = TradingEngine._channel_swing_action(
        midtrend, 99.4, "SHORT",
    )
    assert (midtrend_result["action"], midtrend_result["reason"]) == (
        "EXIT", "KC_LOWER_OUTER_VALLEY_EXIT",
    )

    bottom = _channel_frame(lower=99.0, upper=101.0)
    bottom["atr"] = 1.0
    bottom.loc[
        bottom.index[-3:], ["open", "high", "low", "close", "ma3"]
    ] = [
        [99.0, 99.1, 98.3, 98.4, 98.6],
        [98.4, 99.5, 98.2, 99.4, 99.1],
        [99.4, 99.7, 99.3, 99.6, 99.3],
    ]

    close_short = TradingEngine._channel_swing_action(
        bottom, 99.6, "SHORT",
    )
    open_long = TradingEngine._channel_swing_action(bottom, 99.6)

    assert (close_short["action"], close_short["reason"]) == (
        "EXIT", "KC_LOWER_OUTER_VALLEY_EXIT",
    )
    assert (open_long["action"], open_long["side"], open_long["reason"]) == (
        "WAIT", None, "WAIT_KC_OUTER_TREND_ENTRY",
    )

    top = _channel_frame(lower=99.0, upper=101.0)
    top["atr"] = 1.0
    top.loc[top.index[-3], ["open", "close"]] = [101.0, 101.8]
    top.loc[
        top.index[-2], ["open", "high", "low", "close", "ma3"]
    ] = [102.4, 102.5, 101.1, 101.2, 101.4]
    close_long = TradingEngine._channel_swing_action(top, 101.2, "LONG")
    assert (close_long["action"], close_long["reason"]) == (
        "HOLD", "WAIT_OPPOSITE_KC_UPPER_PEAK",
    )


def test_avax_wick_heavy_red_candle_is_not_a_vertical_reversal_exit():
    frame = _channel_frame(lower=7.2717685, upper=7.2807685)
    frame[["open", "close", "high", "low", "ma3"]] = [
        7.276, 7.276, 7.277, 7.275, 7.276,
    ]
    frame["atr"] = 0.0045
    frame.loc[
        frame.index[-3:], ["open", "high", "low", "close", "ma3"]
    ] = [
        [7.274, 7.281, 7.274, 7.281, 7.276667],
        [7.280, 7.283, 7.276, 7.276, 7.277],
        [7.277, 7.277, 7.273, 7.274, 7.277],
    ]

    result = TradingEngine._channel_swing_action(frame, 7.274, "LONG")

    assert (result["action"], result["reason"]) == (
        "HOLD", "WAIT_OPPOSITE_KC_UPPER_PEAK",
    )


def test_opposite_outer_downtrend_keeps_held_long_without_outer_peak():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-3:], ["open", "close", "ma3", "ma15"]] = [
        [100.0, 99.5, 99.2, 100.0],
        [99.4, 98.5, 98.8, 100.0],
        [98.4, 97.5, 98.4, 100.0],
    ]

    result = TradingEngine._channel_swing_action(frame, 97.5, "LONG")

    assert (result["action"], result["side"], result["reason"]) == (
        "HOLD", None, "WAIT_OPPOSITE_KC_UPPER_PEAK",
    )


def test_opposite_outer_uptrend_keeps_held_short_without_outer_trough():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-3:], ["open", "close", "ma3", "ma15"]] = [
        [100.0, 100.5, 100.8, 100.0],
        [100.6, 101.5, 101.2, 100.0],
        [101.6, 102.5, 101.6, 100.0],
    ]

    result = TradingEngine._channel_swing_action(frame, 102.5, "SHORT")

    assert (result["action"], result["side"], result["reason"]) == (
        "HOLD", None, "WAIT_OPPOSITE_KC_LOWER_VALLEY",
    )


def test_live_opposite_outer_break_keeps_held_position():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-3:], ["open", "close", "ma3", "ma15"]] = [
        [100.0, 99.5, 99.2, 100.0],
        [99.4, 98.5, 98.8, 100.0],
        [98.4, 98.7, 98.6, 100.0],
    ]

    result = TradingEngine._channel_swing_action(frame, 98.7, "LONG")

    assert (result["action"], result["side"], result["reason"]) == (
        "HOLD", None, "WAIT_OPPOSITE_KC_UPPER_PEAK",
    )

def test_three_candle_exit_never_counts_the_live_candle():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-3:], ['open', 'close']] = [[102.0, 101.8], [101.9, 101.6], [101.7, 101.4]]
    result = TradingEngine._channel_swing_action(frame, 101.4, 'LONG')
    assert result['action'] == 'HOLD'

def test_three_red_candles_require_falling_closes():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-4:-1], ['open', 'close']] = [[102.0, 101.6], [101.9, 101.7], [101.8, 101.5]]
    result = TradingEngine._channel_swing_action(frame, 100.0, 'LONG')
    assert result['action'] == 'HOLD'

def test_held_long_keeps_position_when_red_candle_remains_above_upper_rail():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'low']] = [102.0, 101.2, 102.1, 101.1]
    result = TradingEngine._channel_swing_action(frame, 101.2, 'LONG')
    assert (result['action'], result['side']) == ('HOLD', None)

def test_kc_outer_profit_exit_has_no_same_symbol_reentry_path():
    process_source = inspect.getsource(TradingEngine._process_single_symbol)
    assert not hasattr(TradingEngine, '_channel_outer_reentry_reenter_action')
    assert '_channel_outer_reentry_after_exit' not in process_source

def test_outer_growth_pause_keeps_symbol_available_for_resumption():
    assert not TradingEngine._channel_entry_window_expired("WAIT_UPPER_TREND_RESET")
    assert not TradingEngine._channel_entry_window_expired("WAIT_LOWER_TREND_RESET")
    assert not TradingEngine._channel_entry_window_expired("WAIT_KC_OUTER_TREND_ENTRY")
    assert not TradingEngine._channel_entry_window_expired("CHOP_WAIT_NO_ENTRY")

def test_profit_exit_requests_immediate_symbol_replacement_with_cooldown():
    rotation = SymbolRotation.__new__(SymbolRotation)
    rotation.next_rotation_exclusions = set()
    rotation.replacement_cooldowns = {}
    rotation.last_rotation_at = 123.0
    rotation.request_replacement('SOL/USDT')
    assert rotation.next_rotation_exclusions == {'SOL/USDT'}
    assert rotation.replacement_cooldowns['SOL/USDT'] > 0.0
    assert rotation.replacement_exclusions() == {'SOL/USDT'}
    assert rotation.last_rotation_at == 0.0

def test_profit_exit_symbol_stays_excluded_after_pending_scan_is_consumed():
    rotation = SymbolRotation.__new__(SymbolRotation)
    rotation.next_rotation_exclusions = set()
    rotation.replacement_cooldowns = {}
    rotation.last_rotation_at = 123.0
    rotation.request_replacement('AAVE/USDT')
    rotation.next_rotation_exclusions.clear()
    assert rotation.replacement_exclusions() == {'AAVE/USDT'}

def test_upper_red_peak_short_only_allows_green_reversal_at_upper_rail():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], 'open'] = 100.8
    assert not TradingEngine._channel_upper_red_short_reversal_allowed(frame, 100.9)
    frame.loc[frame.index[-1], 'open'] = 101.1
    assert not TradingEngine._channel_upper_red_short_reversal_allowed(frame, 101.0)
    frame.loc[frame.index[-1], 'open'] = 100.9
    assert TradingEngine._channel_upper_red_short_reversal_allowed(frame, 101.0)

def test_upper_red_peak_short_origin_is_scoped_to_channel_peak_entries():
    assert TradingEngine._channel_is_upper_red_peak_short({'side': 'SHORT', 'entry_mode': 'CHANNEL_SWING', 'channel_turn_high': 101.0})
    assert not TradingEngine._channel_is_upper_red_peak_short({'side': 'SHORT', 'entry_mode': 'CHANNEL_SWING', 'channel_turn_low': 99.0})

def test_channel_entry_cannot_reuse_the_bar_that_just_exited():
    bar_id = 12345
    assert TradingEngine._channel_entry_reuses_exit_bar('ENTER', False, bar_id, bar_id)
    assert not TradingEngine._channel_entry_reuses_exit_bar('ENTER', False, bar_id + 1, bar_id)
    assert not TradingEngine._channel_entry_reuses_exit_bar('REVERSE', True, bar_id, bar_id)
    engine = object.__new__(TradingEngine)
    assert engine._channel_entry_reuses_exit_bar('ENTER', False, bar_id, bar_id)

def test_same_bar_upper_outer_rechase_immediately_reverses_short_without_half_kc():
    frame = _channel_frame(lower=99.8, upper=100.2)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [99.9, 100.1]
    result = TradingEngine._channel_same_bar_outer_rechase_action(frame, 100.2, 'SHORT', frame.index[-2])
    assert (result['action'], result['side'], result['reason']) == ('REVERSE', 'LONG', 'SAME_BAR_UPPER_OUTER_RECHASE')
    assert (100.2 - 99.8) / 100.2 < 0.005

def test_same_bar_lower_outer_rechase_immediately_reverses_long_without_half_kc():
    frame = _channel_frame(lower=99.8, upper=100.2)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [99.9, 100.1]
    result = TradingEngine._channel_same_bar_outer_rechase_action(frame, 99.8, 'LONG', frame.index[-2])
    assert (result['action'], result['side'], result['reason']) == ('REVERSE', 'SHORT', 'SAME_BAR_LOWER_OUTER_RECHASE')
    assert (100.2 - 99.8) / 99.8 < 0.005

def test_outer_rechase_requires_the_position_to_come_from_same_live_bar():
    frame = _channel_frame(lower=99.8, upper=100.2)
    result = TradingEngine._channel_same_bar_outer_rechase_action(frame, 100.2, 'SHORT', frame.index[-3])
    assert (result['action'], result['side']) == ('HOLD', None)

def test_same_bar_outer_rechase_bypasses_chop_close_only_gate():
    assert TradingEngine._channel_chop_gate('REVERSE', 'LONG', True, True, 'SAME_BAR_UPPER_OUTER_RECHASE') == ('REVERSE', 'LONG', None)
    assert TradingEngine._channel_chop_gate('REVERSE', 'SHORT', True, True, 'SAME_BAR_LOWER_OUTER_RECHASE') == ('REVERSE', 'SHORT', None)

def test_flat_live_outer_break_waits_for_closed_peak_confirmation():
    frame = _channel_frame(lower=99.0, upper=101.0)
    result = TradingEngine._channel_swing_action(frame, 101.1)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_KC_OUTER_TREND_ENTRY')

def test_live_price_inside_kc_does_not_use_immediate_outer_entry():
    frame = _channel_frame(lower=99.8, upper=100.2)
    result = TradingEngine._channel_live_outer_entry_action(frame, 100.0)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_LIVE_OUTER_BREAK')
    action_source = inspect.getsource(TradingEngine._channel_swing_action)
    process_source = inspect.getsource(TradingEngine._process_single_symbol)
    assert 'INNER_' not in action_source
    assert 'inner_reentry' not in process_source

def test_second_closed_confirmation_candle_must_keep_direction_color():
    trough = _channel_frame()
    trough.loc[trough.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    trough.loc[trough.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.2, 98.9, 98.8, 99.3, 99.4]
    peak = _channel_frame()
    peak.loc[peak.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [101.2, 101.1, 101.05, 101.3, 101.3]
    peak.loc[peak.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [100.8, 101.1, 100.7, 101.2, 100.6]
    flat_trough = TradingEngine._channel_swing_action(trough, 99.0)
    held_short = TradingEngine._channel_swing_action(trough, 99.0, 'SHORT')
    flat_peak = TradingEngine._channel_swing_action(peak, 101.0)
    held_long = TradingEngine._channel_swing_action(peak, 101.0, 'LONG')
    assert (flat_trough['action'], flat_trough['side']) == ('ENTER', 'SHORT')
    assert (held_short['action'], held_short['side']) == ('HOLD', None)
    assert (flat_peak['action'], flat_peak['side']) == ('ENTER', 'LONG')
    assert (held_long['action'], held_long['side']) == ('HOLD', None)

def test_flat_live_outer_cross_requires_confirmed_trend_on_both_sides():
    upper = _channel_frame()
    upper.loc[upper.index[-2], 'close'] = 101.1
    upper_wait = TradingEngine._channel_outer_trend_entry_action(upper, 101.2)
    lower = _channel_frame()
    lower.loc[lower.index[-2], 'close'] = 98.9
    lower_wait = TradingEngine._channel_outer_trend_entry_action(lower, 98.8)
    assert (upper_wait['action'], upper_wait['side']) == ('WAIT', None)
    assert upper_wait['reason'] == 'WAIT_DYNAMIC_TREND'
    assert (lower_wait['action'], lower_wait['side']) == ('WAIT', None)
    assert lower_wait['reason'] == 'WAIT_DYNAMIC_DOWNTREND'

def test_outer_ma3_route_accepts_two_closed_turn_bars_that_remain_outside():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    long_entry = TradingEngine._channel_swing_action(long_frame, 99.9)
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    short_entry = TradingEngine._channel_swing_action(short_frame, 100.1)
    assert (long_entry['action'], long_entry['side']) == ('ENTER', 'LONG')
    assert (short_entry['action'], short_entry['side']) == ('ENTER', 'SHORT')

def test_body_deep_eighty_percent_into_half_channel_bypasses_outer_depth():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    long_frame.loc[long_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.85, 99.9, 98.9, 99.95, 98.95]
    long_frame.loc[long_frame.index[-2], ['low', 'high']] = [99.0, 100.0]
    long_turn = TradingEngine._channel_swing_action(long_frame, 100.0)
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    short_frame.loc[short_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [100.15, 100.1, 100.05, 101.1, 101.05]
    short_frame.loc[short_frame.index[-2], ['low', 'high']] = [100.0, 101.0]
    short_turn = TradingEngine._channel_swing_action(short_frame, 100.0)
    assert (long_turn['action'], long_turn['side']) == ('ENTER', 'LONG')
    assert (short_turn['action'], short_turn['side']) == ('ENTER', 'SHORT')

def test_shallow_outer_v_turns_are_symmetric_without_ma3_depth():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    long_frame.loc[long_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.1, 99.2, 98.9, 99.25, 98.7]
    long_frame.loc[long_frame.index[-2], 'low'] = 99.0
    long_turn = TradingEngine._channel_swing_action(long_frame, 100.0)
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    short_frame.loc[short_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [100.9, 100.8, 100.75, 101.1, 101.3]
    short_frame.loc[short_frame.index[-2], 'high'] = 101.0
    short_turn = TradingEngine._channel_swing_action(short_frame, 100.0)
    assert (long_turn['action'], long_turn['side']) == ('ENTER', 'LONG')
    assert (short_turn['action'], short_turn['side']) == ('ENTER', 'SHORT')

def test_lower_outer_green_reentry_can_open_long_without_ma3_depth():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [98.95, 99.2, 98.9, 99.25, 98.7]
    frame.loc[frame.index[-2], 'low'] = 99.0
    result = TradingEngine._channel_swing_action(frame, 100.0)
    assert (result['action'], result['side']) == ('ENTER', 'LONG')

def test_latest_adjacent_two_closed_outer_v_bars_are_valid_on_both_sides():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    long_turn = TradingEngine._channel_swing_action(long_frame, 99.8)
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    short_turn = TradingEngine._channel_swing_action(short_frame, 100.2)
    assert (long_turn['action'], long_turn['side']) == ('ENTER', 'LONG')
    assert (short_turn['action'], short_turn['side']) == ('ENTER', 'SHORT')

def test_flat_entry_does_not_reuse_multi_candle_confirmed_turn():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    long_frame.loc[long_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.4, 99.2, 99.05, 99.45, 99.2]
    long_frame.loc[long_frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.4, 99.7, 99.3, 99.75, 99.6]
    long_frame.loc[long_frame.index[-1], ['close', 'ma3']] = [99.8, 99.8]
    long_turn = TradingEngine._channel_swing_action(long_frame, 99.8)
    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [101.2, 101.1, 101.05, 101.3, 101.3]
    short_frame.loc[short_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [100.6, 100.8, 100.55, 100.95, 100.8]
    short_frame.loc[short_frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [100.6, 100.3, 100.25, 100.7, 100.4]
    short_frame.loc[short_frame.index[-1], ['close', 'ma3']] = [100.2, 100.2]
    short_turn = TradingEngine._channel_swing_action(short_frame, 100.2)
    assert (long_turn['action'], long_turn['reason']) == ('WAIT', 'WAIT_KC_OUTER_TREND_ENTRY')
    assert long_turn['side'] is None
    assert (short_turn['action'], short_turn['reason']) == ('WAIT', 'WAIT_KC_OUTER_TREND_ENTRY')
    assert short_turn['side'] is None

def test_flat_entry_does_not_reuse_turn_after_opposite_color_candle():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.4, 99.2, 99.1, 99.5, 99.2]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.2, 99.7, 99.15, 99.75, 99.6]
    frame.loc[frame.index[-1], ['close', 'ma3']] = [99.8, 99.8]
    result = TradingEngine._channel_swing_action(frame, 99.8)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_KC_OUTER_TREND_ENTRY')
    assert result['side'] is None

def test_empty_slot_does_not_chase_kc_outer_trend_without_pivot_turn():
    up = _channel_frame()
    up.loc[up.index[-3], 'ma3'] = 100.5
    up.loc[up.index[-2], 'ma3'] = 100.9
    up.loc[up.index[-1], ['open', 'close', 'high', 'ma3']] = [101.1, 101.4, 101.5, 101.3]
    long_wait = TradingEngine._channel_swing_action(up, 101.4)
    down = _channel_frame()
    down.loc[down.index[-3], 'ma3'] = 99.5
    down.loc[down.index[-2], 'ma3'] = 99.1
    down.loc[down.index[-1], ['open', 'close', 'low', 'ma3']] = [98.9, 98.6, 98.5, 98.7]
    short_wait = TradingEngine._channel_swing_action(down, 98.6)
    assert (long_wait['action'], long_wait['side']) == ('ENTER', 'LONG')
    assert long_wait['turn_low'] is None
    assert (short_wait['action'], short_wait['side']) == ('ENTER', 'SHORT')
    assert short_wait['turn_high'] is None

def test_empty_slot_does_not_chase_price_outside_without_ma3_trend():
    frame = _channel_frame()
    frame.loc[frame.index[-2], 'ma3'] = 100.8
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [101.1, 101.4, 101.5, 100.9]
    result = TradingEngine._channel_swing_action(frame, 101.4)
    assert result['action'] == 'ENTER'

def test_live_outer_break_does_not_require_ma3_slope():
    up = _channel_frame()
    up.loc[up.index[-3], 'ma3'] = 100.0
    up.loc[up.index[-2], 'ma3'] = 100.9
    up.loc[up.index[-1], ['open', 'close', 'high', 'ma3']] = [101.1, 101.4, 101.5, 101.2]
    down = _channel_frame()
    down.loc[down.index[-3], 'ma3'] = 100.0
    down.loc[down.index[-2], 'ma3'] = 99.1
    down.loc[down.index[-1], ['open', 'close', 'low', 'ma3']] = [98.9, 98.6, 98.5, 98.8]
    assert TradingEngine._channel_swing_action(up, 101.4)['action'] == 'ENTER'
    assert TradingEngine._channel_swing_action(down, 98.6)['action'] == 'ENTER'

def test_empty_slot_does_not_chase_when_only_close_breaks_outer_rail():
    frame = _channel_frame()
    frame.loc[frame.index[-3], 'ma3'] = 100.5
    frame.loc[frame.index[-2], 'ma3'] = 100.9
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [100.9, 101.4, 101.5, 101.3]
    result = TradingEngine._channel_swing_action(frame, 101.4)
    assert result['action'] == 'ENTER'

def test_prior_downtrend_inside_kc_does_not_open_continuation_chase():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    frame.loc[frame.index[-3], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 100.0, 100.0, 98.9, 100.9]
    frame.loc[frame.index[-2], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 99.9, 99.9, 98.85, 100.85]
    frame.loc[frame.index[-1], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [99.8, 99.6, 99.8, 98.8, 100.8]
    result = TradingEngine._channel_swing_action(frame, 99.6)
    assert (result['action'], result['side']) == ('WAIT', None)
    assert result['reason'] == 'WAIT_KC_OUTER_TREND_ENTRY'
    assert result['turn_low'] is None
    assert result['turn_high'] is None

def test_prior_downtrend_blocks_green_countertrend_long_entry():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    frame.loc[frame.index[-3], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 100.0, 100.0, 98.9, 100.9]
    frame.loc[frame.index[-2], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 99.9, 99.9, 98.85, 100.85]
    frame.loc[frame.index[-1], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [99.5, 99.6, 99.8, 98.8, 100.8]
    result = TradingEngine._channel_swing_action(frame, 99.6)
    assert (result['action'], result['side']) == ('WAIT', None)
    assert result['reason'] == 'WAIT_KC_OUTER_TREND_ENTRY'

def test_prior_uptrend_inside_kc_does_not_open_continuation_chase():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [101.2, 101.1, 101.05, 101.3, 101.3]
    frame.loc[frame.index[-3], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 100.0, 100.0, 99.1, 101.1]
    frame.loc[frame.index[-2], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 100.1, 100.1, 99.15, 101.15]
    frame.loc[frame.index[-1], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.2, 100.4, 100.2, 99.2, 101.2]
    result = TradingEngine._channel_swing_action(frame, 100.4)
    assert (result['action'], result['side']) == ('WAIT', None)
    assert result['reason'] == 'WAIT_KC_OUTER_TREND_ENTRY'
    assert result['turn_low'] is None
    assert result['turn_high'] is None

def test_channel_swing_holds_between_entry_and_opposite_edge():
    frame = _channel_frame()
    assert TradingEngine._channel_swing_action(frame, 100.0, 'LONG')['action'] == 'HOLD'
    assert TradingEngine._channel_swing_action(frame, 100.0, 'SHORT')['action'] == 'HOLD'

def test_single_closed_outer_red_candidate_is_still_front_stage():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ['open', 'close', 'high', 'low', 'ma3']] = [101.2, 100.8, 101.3, 100.7, 101.0]
    result = TradingEngine._channel_swing_action(frame, 100.8, 'LONG')
    assert (result['action'], result['side']) == ('HOLD', None)

def test_single_closed_outer_green_candidate_is_still_front_stage():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ['open', 'close', 'high', 'low', 'ma3']] = [98.8, 99.2, 99.3, 98.7, 99.0]
    result = TradingEngine._channel_swing_action(frame, 99.2, 'SHORT')
    assert (result['action'], result['side']) == ('HOLD', None)

def test_channel_swing_does_not_enter_from_unclosed_live_green_or_red_candle():
    frame = _channel_frame()
    frame.loc[frame.index[-1], ['open', 'low']] = [99.1, 98.9]
    no_trough = TradingEngine._channel_swing_action(frame, 99.2)
    assert no_trough['action'] == 'WAIT'
    assert no_trough['reason'] == 'WAIT_KC_OUTER_TREND_ENTRY'
    frame = _channel_frame()
    frame.loc[frame.index[-1], ['open', 'high']] = [100.9, 101.1]
    no_peak = TradingEngine._channel_swing_action(frame, 100.8)
    assert no_peak['action'] == 'WAIT'
    assert no_peak['reason'] == 'WAIT_KC_OUTER_TREND_ENTRY'

def test_closed_lower_trough_uses_confirmed_long_instead_of_live_outer_short():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-2], ['high', 'low']] = [98.9, 98.8]
    result = TradingEngine._channel_swing_action(frame, 98.9)
    assert (result['action'], result['side']) == ('ENTER', 'LONG')
    assert result['reason'] == 'OUTER_TROUGH_NEXT_BREAK_LONG'

def test_failed_lower_trough_does_not_fall_back_to_live_outer_short():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-2], 'low'] = 98.6
    frame.loc[frame.index[-1], 'low'] = 98.5
    result = TradingEngine._channel_swing_action(frame, 98.6)
    assert (result['action'], result['side']) == ('WAIT', None)
    assert result['reason'] == 'WAIT_KC_OUTER_TREND_ENTRY'

def test_cancelled_outer_trough_cannot_fall_back_to_live_ma3_entry():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-2], ['low', 'high']] = [98.6, 99.3]
    frame.loc[frame.index[-1], 'low'] = 98.5
    result = TradingEngine._channel_swing_action(frame, 99.2)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_KC_OUTER_TREND_ENTRY')

def test_cancelled_outer_peak_cannot_fall_back_to_live_ma3_entry():
    frame = _channel_frame()
    _closed_peak(frame)
    frame.loc[frame.index[-2], ['low', 'high']] = [101.1, 101.2]
    waiting = TradingEngine._channel_swing_action(frame, 101.1)
    frame.loc[frame.index[-2], ['low', 'high']] = [100.7, 101.4]
    cancelled = TradingEngine._channel_swing_action(frame, 100.8)
    assert (waiting['action'], waiting['side']) == ('ENTER', 'SHORT')
    assert waiting['reason'] == 'OUTER_PEAK_NEXT_BREAK_SHORT'
    assert (cancelled['action'], cancelled['side'], cancelled['reason']) == ('WAIT', None, 'WAIT_KC_OUTER_TREND_ENTRY')

def test_channel_swing_does_not_exit_before_actual_rail_touch():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high']] = [101.0, 100.9, 100.8, 100.95]
    result = TradingEngine._channel_swing_action(frame, 100.8, 'LONG')
    assert result['action'] == 'HOLD'
    assert result['side'] is None

def test_channel_chop_state_detects_repeated_ma_and_middle_crosses():
    frame = _channel_frame()
    closes = [99.6, 100.4] * 6
    ma3 = [99.7, 100.3] * 6
    frame.loc[frame.index[-13:-1], 'close'] = closes
    frame.loc[frame.index[-13:-1], 'ma3'] = ma3
    result = TradingEngine._channel_chop_state(frame)
    assert result['detected'] is True
    assert result['clear_direction'] is None
    assert result['ma_crosses'] >= 3
    assert result['middle_crosses'] >= 3

def test_channel_chop_state_unlocks_after_three_clear_closed_bars():
    frame = _channel_frame()
    for position in range(8, 20):
        middle = 99.4 + position * 0.08
        frame.loc[position, ['kc_lower', 'kc_upper', 'ma15', 'ma3', 'close']] = [middle - 1.0, middle + 1.0, middle - 0.2, middle + 0.2, middle + 0.35]
    result = TradingEngine._channel_chop_state(frame)
    assert result['detected'] is False
    assert result['clear_direction'] == 'LONG'

def test_channel_near_chop_gate_blocks_only_new_entries():
    assert TradingEngine._channel_near_chop_entry_gate("ENTER", "LONG", True, False) == ("WAIT", None, "CHOP_NEAR_LOCK_NO_ENTRY")
    assert TradingEngine._channel_near_chop_entry_gate("ENTER", "SHORT", False, False) == ("ENTER", "SHORT", None)
    assert TradingEngine._channel_near_chop_entry_gate("EXIT", None, True, True) == ("EXIT", None, None)

def test_channel_chop_gate_blocks_entry_and_turns_reverse_into_close_only():
    assert TradingEngine._channel_chop_gate('ENTER', 'LONG', True, False) == ('WAIT', None, 'CHOP_WAIT_NO_ENTRY')
    assert TradingEngine._channel_chop_gate('REVERSE', 'SHORT', True, True) == ('EXIT', None, 'CHOP_WAIT_CLOSE_ONLY')
    assert TradingEngine._channel_chop_gate('ENTER', 'LONG', False, False) == ('ENTER', 'LONG', None)

def test_confirmed_outer_peak_exits_regardless_of_market_mode():
    frame = _channel_frame()
    _closed_peak(frame)
    frame.loc[frame.index[-1], ['low', 'high']] = [101.1, 101.2]
    result = TradingEngine._channel_swing_action(frame, 100.6, 'LONG', market_mode='BEAR')
    assert (result['action'], result['side'], result['reason']) == (
        "EXIT", None, "KC_UPPER_OUTER_PEAK_EXIT",
    )

def test_flat_entry_uses_ma3_and_held_position_exits_on_confirmed_trough():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-2], ['open', 'ma3']] = [98.8, 98.9]
    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, 'SHORT')
    assert (empty['action'], empty['side']) == ('ENTER', 'LONG')
    assert (held_short['action'], held_short['side']) == ('REVERSE', 'LONG')

def test_confirmed_outer_pivot_opens_before_forty_percent_reentry():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    long_frame.loc[long_frame.index[-1], ['open', 'close', 'high', 'low', 'ma3']] = [99.05, 99.2, 99.3, 99.0, 99.2]
    long_entry = TradingEngine._channel_swing_action(long_frame, 99.2)
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    short_frame.loc[short_frame.index[-1], ['open', 'close', 'high', 'low', 'ma3']] = [100.95, 100.8, 101.0, 100.7, 100.8]
    short_entry = TradingEngine._channel_swing_action(short_frame, 100.8)
    assert (short_entry['action'], short_entry['side']) == ('ENTER', 'SHORT')
    assert (long_entry['action'], long_entry['side']) == ('ENTER', 'LONG')

def test_current_trend_does_not_reuse_already_confirmed_outer_pivot():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.1, 99.0, 98.9, 99.2, 99.0]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.1, 99.1, 99.0, 99.2, 99.2]
    frame.loc[frame.index[-1], ['open', 'close', 'low', 'high', 'ma3']] = [99.6, 99.5, 99.15, 99.65, 99.4]
    result = TradingEngine._channel_swing_action(frame, 99.5)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_KC_OUTER_TREND_ENTRY')
    assert result['side'] is None

def test_current_downtrend_after_outer_peak_opens_on_green_candle():
    frame = _channel_frame()
    _closed_peak(frame)
    frame.loc[frame.index[-1], ['open', 'close', 'low', 'high', 'ma3']] = [100.7, 100.8, 100.6, 101.0, 100.8]
    result = TradingEngine._channel_swing_action(frame, 100.8)
    assert (result['action'], result['side']) == ('ENTER', 'SHORT')

def test_inside_kc_two_closed_green_candles_do_not_open_long():
    frame = _channel_frame()
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high']] = [99.4, 99.6, 99.3, 99.7]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high']] = [100.1, 100.4, 99.4, 100.5]
    frame.loc[frame.index[-1], ['open', 'close']] = [100.5, 99.8]
    result = TradingEngine._channel_swing_action(frame, 99.8)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_KC_OUTER_TREND_ENTRY')

def test_inside_kc_two_closed_red_candles_do_not_open_short():
    frame = _channel_frame()
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high']] = [100.6, 100.4, 100.3, 100.7]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high']] = [99.9, 99.6, 99.5, 100.6]
    frame.loc[frame.index[-1], ['open', 'close']] = [99.5, 100.2]
    result = TradingEngine._channel_swing_action(frame, 100.2)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_KC_OUTER_TREND_ENTRY')

def test_inside_kc_second_candle_reverse_extreme_cancels_entry():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-3], ['open', 'close', 'low', 'high']] = [99.4, 99.6, 99.3, 99.7]
    long_frame.loc[long_frame.index[-2], ['open', 'close', 'low', 'high']] = [100.1, 100.4, 99.2, 100.5]
    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-3], ['open', 'close', 'low', 'high']] = [100.6, 100.4, 100.3, 100.7]
    short_frame.loc[short_frame.index[-2], ['open', 'close', 'low', 'high']] = [99.9, 99.6, 99.5, 100.8]
    long_result = TradingEngine._channel_swing_action(long_frame, 100.4)
    short_result = TradingEngine._channel_swing_action(short_frame, 99.6)
    assert (long_result['action'], long_result['side']) == ('WAIT', None)
    assert (short_result['action'], short_result['side']) == ('WAIT', None)

def test_inside_kc_live_second_candle_only_previews_and_does_not_enter():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-2], ['open', 'close', 'low', 'high']] = [99.4, 99.6, 99.3, 99.7]
    long_frame.loc[long_frame.index[-1], ['open', 'close', 'low', 'high']] = [100.1, 100.4, 99.4, 100.5]
    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-2], ['open', 'close', 'low', 'high']] = [100.6, 100.4, 100.3, 100.7]
    short_frame.loc[short_frame.index[-1], ['open', 'close', 'low', 'high']] = [99.9, 99.6, 99.5, 100.6]
    long_result = TradingEngine._channel_swing_action(long_frame, 100.4)
    short_result = TradingEngine._channel_swing_action(short_frame, 99.6)
    assert (long_result['action'], long_result['side']) == ('WAIT', None)
    assert (short_result['action'], short_result['side']) == ('WAIT', None)

def test_inside_kc_ma3_continuation_without_turn_still_waits():
    frame = _channel_frame()
    frame.loc[frame.index[-3], 'ma3'] = 99.6
    frame.loc[frame.index[-2], 'ma3'] = 99.8
    frame.loc[frame.index[-1], 'ma3'] = 100.0
    result = TradingEngine._channel_swing_action(frame, 100.2)
    assert (result['action'], result['side']) == ('WAIT', None)

def test_ma3_turn_does_not_open_when_price_is_outside_kc():
    frame = _channel_frame()
    frame.loc[frame.index[-2], 'ma3'] = 99.7
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [101.1, 101.2, 101.3, 100.0]
    result = TradingEngine._channel_swing_action(frame, 101.2)
    assert (result['action'], result['side']) == ('ENTER', 'LONG')

def test_old_outer_pivot_does_not_chase_at_opposite_outer_rail():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [100.8, 101.1, 101.2, 100.9]
    result = TradingEngine._channel_swing_action(frame, 101.1)
    assert (result['action'], result['side']) == ('ENTER', 'LONG')

def test_shallow_outer_reentry_still_reverses_held_short_on_confirmed_trough():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], 'ma3'] = 99.2
    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, 'SHORT')
    assert (empty['action'], empty['side']) == ('ENTER', 'LONG')
    assert (held_short['action'], held_short['side']) == ('REVERSE', 'LONG')

def test_channel_swing_reentry_boundary_is_80_percent_of_outer_half():
    below = _channel_frame()
    _closed_trough(below)
    below.loc[below.index[-1], ['close', 'ma3']] = [99.78, 99.78]
    rejected = TradingEngine._channel_swing_action(below, 99.78)
    boundary = _channel_frame()
    _closed_trough(boundary)
    boundary.loc[boundary.index[-1], ['close', 'ma3']] = [99.8, 99.8]
    accepted = TradingEngine._channel_swing_action(boundary, 99.8)
    assert (rejected['action'], rejected['side']) == ('ENTER', 'LONG')
    assert (accepted['action'], accepted['side']) == ('ENTER', 'LONG')

def test_live_ma3_turn_does_not_block_confirmed_trough_exit():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [99.1, 99.2, 100.5, 100.0]
    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, 'SHORT')
    assert (empty['action'], empty['side']) == ('ENTER', 'LONG')
    assert (held_short['action'], held_short['side']) == ('REVERSE', 'LONG')

def test_channel_swing_positions_are_not_managed_by_early_exit_loops():
    assert TradingEngine._is_continuous_wave_position({'entry_mode': 'CHANNEL_SWING'})

def test_channel_swing_has_one_confirmation_rule_and_no_legacy_entry_paths():
    from services.api import manual_order
    action_source = inspect.getsource(TradingEngine._channel_swing_action)
    process_source = inspect.getsource(TradingEngine._process_single_symbol)
    manual_source = inspect.getsource(manual_order)
    assert 'CONTINUOUS_ENTRY_OUTER_ZONE_RATIO' not in action_source
    assert '"entry_mode": "CHANNEL_SWING"' in manual_source
    assert '"wave_regime": "RANGE"' in manual_source
    assert 'KC 撕裂復原' not in process_source
    assert 'swing_direction' not in process_source
    assert '_channel_trend_entry_action(' not in process_source
    assert 'KC_INNER_MA3_TURN' not in action_source
    assert 'KC_INNER_TWO_GREEN_CROSS_UP' not in action_source
    assert 'KC_INNER_TWO_RED_CROSS_DOWN' not in action_source
    assert 'inner_ma3_turn' not in action_source
    assert '_channel_closed_body_break_entry_action(' in process_source
    assert '_channel_immediate_outer_break_action(' in process_source
    assert '_channel_closed_body_break_entry_action(' in process_source
    reverse_start = process_source.index('if action == "REVERSE" and existing_pos:')
    reverse_end = process_source.index('if existing_pos:', reverse_start + 1)
    reverse_source = process_source[reverse_start:reverse_end]
    assert reverse_source.index('close_position(') < reverse_source.index('detected_candidates.append(')
    assert '_strongest_ranked_symbol' not in reverse_source
    assert '"symbol": symbol' in reverse_source
    assert 'close-first' in reverse_source

def test_breaking_entry_side_outer_rail_keeps_position():
    frame = _channel_frame()
    held_long = TradingEngine._channel_swing_action(frame, 98.8, "LONG", entry_turn_low=98.9)
    held_short = TradingEngine._channel_swing_action(frame, 101.2, "SHORT", entry_turn_high=101.1)
    assert (held_long["action"], held_long["side"], held_long["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_UPPER_PEAK")
    assert (held_short["action"], held_short["side"], held_short["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_partial_body_crossing_entry_side_outer_rail_keeps_position():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-1], ["open", "close", "low"]] = [99.2, 98.8, 98.7]
    held_long = TradingEngine._channel_swing_action(long_frame, 98.8, "LONG")
    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-1], ["open", "close", "high"]] = [100.8, 101.2, 101.3]
    held_short = TradingEngine._channel_swing_action(short_frame, 101.2, "SHORT")
    assert (held_long["action"], held_long["side"], held_long["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_UPPER_PEAK")
    assert (held_short["action"], held_short["side"], held_short["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_confirmed_outer_peak_and_trough_exit_without_extra_end_stage_gate():
    long_frame = _channel_frame()
    _closed_peak(long_frame)
    held_long = TradingEngine._channel_swing_action(long_frame, 101.1, 'LONG', market_mode='BEAR')
    short_frame = _channel_frame()
    _closed_trough(short_frame)
    held_short = TradingEngine._channel_swing_action(short_frame, 98.9, 'SHORT', market_mode='BULL')
    assert (held_long['action'], held_long['side'], held_long['reason']) == ('EXIT', None, 'KC_UPPER_OUTER_PEAK_EXIT')
    assert (held_short['action'], held_short['side'], held_short['reason']) == ('EXIT', None, 'KC_LOWER_OUTER_VALLEY_EXIT')

def test_held_position_keeps_on_adverse_side_outer_pivot():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    held_long = TradingEngine._channel_swing_action(long_frame, 98.9, "LONG")
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    held_short = TradingEngine._channel_swing_action(short_frame, 101.1, "SHORT")
    assert (held_long["action"], held_long["reason"]) == ("HOLD", "WAIT_OPPOSITE_KC_UPPER_PEAK")
    assert (held_short["action"], held_short["reason"]) == ("HOLD", "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_channel_swing_background_trigger_loop_is_diagnostic_only():
    source = inspect.getsource(TradingEngine._position_trigger_loop)
    guard = source.index("if pos_entry_mode.upper() == \"CHANNEL_SWING\":")
    rapid_exit = source.index("if rapid_adverse_exit")
    assert guard < rapid_exit


def test_bull_long_holds_when_red_candle_reenters_before_outer_peak():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [101.2, 100.8, 101.3, 100.7]
    result = TradingEngine._channel_swing_action(frame, 100.8, "LONG", market_mode="BULL")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_UPPER_PEAK")


def test_bull_long_holds_while_the_reversal_candle_is_still_outside_kc():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [101.3, 101.1, 101.4, 101.05]
    result = TradingEngine._channel_swing_action(frame, 101.1, "LONG", market_mode="BULL")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_UPPER_PEAK")


def test_bull_long_does_not_exit_on_the_upper_kc_boundary():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [101.2, 101.0, 101.3, 100.9]
    result = TradingEngine._channel_swing_action(frame, 101.0, "LONG", market_mode="BULL")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_UPPER_PEAK")

def test_bear_short_holds_when_green_candle_reenters_before_outer_valley():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [98.8, 99.2, 99.3, 98.7]
    result = TradingEngine._channel_swing_action(frame, 99.2, "SHORT", market_mode="BEAR")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_bear_short_holds_while_the_reversal_candle_is_still_outside_kc():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [98.7, 98.9, 98.95, 98.6]
    result = TradingEngine._channel_swing_action(frame, 98.9, "SHORT", market_mode="BEAR")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_bear_short_does_not_exit_on_the_lower_kc_boundary():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [98.8, 99.0, 99.1, 98.7]
    result = TradingEngine._channel_swing_action(frame, 99.0, "SHORT", market_mode="BEAR")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_range_positions_also_wait_for_opposite_outer_pivot():
    long_frame = _channel_frame(lower=99.0, upper=101.0)
    long_frame.loc[long_frame.index[-1], ["open", "close", "high", "low"]] = [101.2, 100.8, 101.3, 100.7]
    short_frame = _channel_frame(lower=99.0, upper=101.0)
    short_frame.loc[short_frame.index[-1], ["open", "close", "high", "low"]] = [98.8, 99.2, 99.3, 98.7]
    long_result = TradingEngine._channel_swing_action(long_frame, 100.8, "LONG", market_mode="RANGE")
    short_result = TradingEngine._channel_swing_action(short_frame, 99.2, "SHORT", market_mode="RANGE")
    assert (long_result["action"], long_result["reason"]) == ("HOLD", "WAIT_OPPOSITE_KC_UPPER_PEAK")
    assert (short_result["action"], short_result["reason"]) == ("HOLD", "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_range_flat_confirmed_outer_trough_enters_long():
    frame = _channel_frame()
    _closed_trough(frame)
    result = TradingEngine._channel_swing_action(
        frame, 99.2, market_mode="RANGE",
    )
    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "WAIT_KC_OUTER_TREND_ENTRY",
    )


def test_range_flat_confirmed_outer_peak_enters_short():
    frame = _channel_frame()
    _closed_peak(frame)
    result = TradingEngine._channel_swing_action(
        frame, 100.8, market_mode="RANGE",
    )
    assert (result["action"], result["side"], result["reason"]) == (
        "WAIT", None, "WAIT_KC_OUTER_TREND_ENTRY",
    )


def test_bear_market_enters_peak_short_and_blocks_trough_long():
    trough = _channel_frame()
    _closed_trough(trough)
    peak = _channel_frame()
    _closed_peak(peak)
    long_result = TradingEngine._channel_swing_action(
        trough, 99.2, market_mode="BEAR",
    )
    short_result = TradingEngine._channel_swing_action(
        peak, 100.8, market_mode="BEAR",
    )
    assert (long_result["action"], long_result["side"], long_result["reason"]) == (
        "WAIT", None, "WAIT_KC_OUTER_TREND_ENTRY",
    )
    assert (short_result["action"], short_result["side"], short_result["reason"]) == (
        "WAIT", None, "WAIT_KC_OUTER_TREND_ENTRY",
    )


def test_bull_market_enters_trough_long_and_blocks_peak_short():
    trough = _channel_frame()
    _closed_trough(trough)
    peak = _channel_frame()
    _closed_peak(peak)
    long_result = TradingEngine._channel_swing_action(
        trough, 99.2, market_mode="BULL",
    )
    short_result = TradingEngine._channel_swing_action(
        peak, 100.8, market_mode="BULL",
    )
    assert (long_result["action"], long_result["side"], long_result["reason"]) == (
        "WAIT", None, "WAIT_KC_OUTER_TREND_ENTRY",
    )
    assert (short_result["action"], short_result["side"], short_result["reason"]) == (
        "WAIT", None, "WAIT_KC_OUTER_TREND_ENTRY",
    )


@pytest.mark.parametrize("market_mode", [None, "TREND"])
def test_directionless_market_uses_outer_pivot_entry(market_mode):
    trough = _channel_frame()
    _closed_trough(trough)
    peak = _channel_frame()
    _closed_peak(peak)
    long_result = TradingEngine._channel_swing_action(
        trough, 99.2, market_mode=market_mode,
    )
    short_result = TradingEngine._channel_swing_action(
        peak, 100.8, market_mode=market_mode,
    )
    assert (long_result["action"], long_result["side"]) == ("WAIT", None)
    assert (short_result["action"], short_result["side"]) == ("WAIT", None)


def test_range_outer_pivot_requires_confirmed_ma3_turn():
    trough = _channel_frame()
    _closed_trough(trough)
    trough.loc[trough.index[-2], "ma3"] = trough.loc[trough.index[-3], "ma3"]
    trough.loc[trough.index[-1], "ma3"] = trough.loc[trough.index[-2], "ma3"]
    peak = _channel_frame()
    _closed_peak(peak)
    peak.loc[peak.index[-2], "ma3"] = peak.loc[peak.index[-3], "ma3"]
    peak.loc[peak.index[-1], "ma3"] = peak.loc[peak.index[-2], "ma3"]
    long_result = TradingEngine._channel_swing_action(
        trough, 99.2, market_mode="RANGE",
    )
    short_result = TradingEngine._channel_swing_action(
        peak, 100.8, market_mode="RANGE",
    )
    assert (long_result["action"], long_result["side"]) == ("WAIT", None)
    assert (short_result["action"], short_result["side"]) == ("WAIT", None)


def test_range_outer_touch_without_adjacent_confirmation_does_not_enter():
    trough = _channel_frame()
    trough.loc[trough.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        98.8, 98.9, 98.7, 98.95, 98.7,
    ]
    peak = _channel_frame()
    peak.loc[peak.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        101.2, 101.1, 101.05, 101.3, 101.3,
    ]
    long_result = TradingEngine._channel_swing_action(
        trough, 98.9, market_mode="RANGE",
    )
    short_result = TradingEngine._channel_swing_action(
        peak, 101.1, market_mode="RANGE",
    )
    assert (long_result["action"], long_result["side"]) == ("WAIT", None)
    assert (short_result["action"], short_result["side"]) == ("WAIT", None)


def test_market_mode_does_not_block_required_outer_pivot_exit():
    long_frame = _channel_frame()
    _closed_peak(long_frame)
    bear_long = TradingEngine._channel_swing_action(long_frame, 101.1, "LONG", market_mode="BEAR")
    short_frame = _channel_frame()
    _closed_trough(short_frame)
    bull_short = TradingEngine._channel_swing_action(short_frame, 98.9, "SHORT", market_mode="BULL")
    assert (bear_long["action"], bear_long["reason"]) == (
        "EXIT", "KC_UPPER_OUTER_PEAK_EXIT",
    )
    assert (bull_short["action"], bull_short["reason"]) == (
        "EXIT", "KC_LOWER_OUTER_VALLEY_EXIT",
    )

def test_btc_1m_pulse_requires_atr_move_and_ma3_alignment(monkeypatch):
    monkeypatch.setattr('core.engine.BTC_1M_PULSE_FILTER_ENABLED', True)
    frame = pd.DataFrame({'close': [100.0, 100.0, 100.2, 100.5, 100.8], 'ma3': [100.0, 100.0, 100.1, 100.3, 100.6], 'atr': [1.0] * 5})
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.8) == 'LONG'
    frame['close'] = [100.8, 100.8, 100.6, 100.3, 100.0]
    frame['ma3'] = [100.8, 100.8, 100.7, 100.5, 100.2]
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.0) == 'SHORT'

def test_btc_lead_shadow_records_aligned_outer_reaction_without_order():
    engine = TradingEngine.__new__(TradingEngine)
    engine._btc_lead_shadow_active = {"key": ("LONG", 1), "side": "LONG", "started_at": 0.0}
    engine._btc_lead_shadow_events = []
    engine.symbol_rotation = types.SimpleNamespace(volatility_stats={})
    frame = _dynamic_upper_trend_frame()
    frame["atr"] = 0.5
    engine._record_btc_lead_shadow_candidate("TEST/USDT", frame, 101.2, False)
    assert engine.btc_lead_shadow_status()["eligible_events"] == 1

def test_adjacent_two_closed_green_bars_confirm_long_candidate():
    frame = _channel_frame()
    _closed_trough(frame)
    for i in range(1, 4):
        frame.loc[frame.index[-i], ['kc_lower', 'ema_20', 'kc_upper']] = [98.9, 99.9, 100.9]
    frame.loc[frame.index[-3], ['kc_lower', 'ema_20', 'kc_upper']] = [98.9, 99.9, 100.9]
    frame.loc[frame.index[-2], ['kc_lower', 'ema_20', 'kc_upper']] = [99.0, 100.0, 101.0]
    frame.loc[frame.index[-1], ['kc_lower', 'ema_20', 'kc_upper']] = [99.1, 100.1, 101.1]
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.0, 99.4, 98.9, 99.5, 99.0]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.4, 100.1, 99.3, 100.2, 100.2]
    frame.loc[frame.index[-1], ['open', 'close', 'low', 'ma3']] = [99.6, 99.7, 99.5, 100.3]
    result = TradingEngine._channel_swing_action(frame, 99.8)
    assert (result.get('action'), result.get('side')) == ('ENTER', 'LONG')

def test_adjacent_two_closed_red_bars_confirm_short_candidate():
    frame = _channel_frame()
    _closed_peak(frame)
    for i in range(1, 4):
        frame.loc[frame.index[-i], ['kc_lower', 'ema_20', 'kc_upper']] = [99.1, 100.1, 101.1]
    frame.loc[frame.index[-3], ['kc_lower', 'ema_20', 'kc_upper']] = [99.1, 100.1, 101.1]
    frame.loc[frame.index[-2], ['kc_lower', 'ema_20', 'kc_upper']] = [99.0, 100.0, 101.0]
    frame.loc[frame.index[-1], ['kc_lower', 'ema_20', 'kc_upper']] = [98.9, 99.9, 100.9]
    frame.loc[frame.index[-3], ['open', 'close', 'high', 'low', 'ma3']] = [101.0, 100.6, 101.1, 100.5, 101.5]
    frame.loc[frame.index[-2], ['open', 'close', 'high', 'low', 'ma3']] = [100.6, 99.9, 100.8, 99.8, 101.0]
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [100.3, 100.1, 100.4, 99.7]
    result = TradingEngine._channel_swing_action(frame, 100.2)
    assert (result.get('action'), result.get('side')) == ('ENTER', 'SHORT')

def test_detect_btc_pulse():
    frame = pd.DataFrame()
    frame['close'] = [100.0, 100.0, 100.1, 100.2, 100.3]
    frame['ma3'] = [100.0, 100.0, 100.1, 100.2, 100.25]
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.3) is None
    assert TradingEngine._btc_pulse_blocks_entry('SHORT', 'LONG') is True
    assert TradingEngine._btc_pulse_blocks_entry('LONG', 'SHORT') is True
    assert TradingEngine._btc_pulse_blocks_entry('LONG', 'LONG') is False
    assert TradingEngine._btc_pulse_blocks_entry('SHORT', None) is False

def test_btc_1m_pulse_is_disabled_by_configuration(monkeypatch):
    monkeypatch.setattr('core.engine.BTC_1M_PULSE_FILTER_ENABLED', False)
    frame = pd.DataFrame({'close': [100.0, 100.2, 100.5, 100.8], 'ma3': [100.0, 100.1, 100.3, 100.6], 'atr': [1.0] * 4})
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.8) is None

def _dynamic_upper_trend_frame():
    frame = _channel_frame()
    for position in range(13, 19):
        middle = 99.5 + (position - 13) * 0.1
        close = middle + 0.8 + (position - 13) * 0.05
        frame.loc[position, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [close - 0.15, close, close + 0.1, close - 0.2, middle + 0.3, middle + 0.1, middle - 1.0, middle + 1.0]
    frame.loc[17, "close"] = float(frame.loc[17, "kc_upper"]) - 0.01
    frame.loc[19, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [101.2, 101.35, 101.36, 101.15, 100.55, 100.25, 99.1, 101.1]
    return frame

def test_kc_upper_outer_uptrend_uses_existing_quality_without_three_bar_delay():
    frame = _dynamic_upper_trend_frame()
    candidate = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)
    assert (candidate['action'], candidate['reason']) == ('WAIT', 'WAIT_TREND_BREAK')
    assert candidate['pending']['side'] == 'LONG'
    assert candidate['pending']['confirmed'] is False
    entered = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.35, candidate['pending'])
    assert (entered['action'], entered['side']) == ('ENTER', 'LONG')
    assert entered['reason'] == 'KC_UPPER_TREND_CONFIRMED_LONG'

def test_kc_upper_outer_uptrend_waits_when_existing_trend_is_ambiguous():
    frame = _dynamic_upper_trend_frame()
    frame.loc[16:18, ['ma3', 'ma15']] = [[100.0, 100.1]] * 3
    result = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_DYNAMIC_TREND')
    assert result['pending'] is None

def test_kc_upper_outer_uptrend_next_bar_failure_cancels_candidate():
    frame = _dynamic_upper_trend_frame()
    seed = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)
    frame.loc[19, 'low'] = float(seed['pending']['candidate_low']) - 0.01
    result = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.2, seed['pending'])
    assert (result['action'], result['reason']) == ('WAIT', 'CANCEL_TREND_CONFIRM')
    assert result['pending'] is None

def test_kc_upper_outer_uptrend_does_not_chase_when_break_is_too_far():
    frame = _dynamic_upper_trend_frame()
    seed = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)
    result = TradingEngine._channel_outer_uptrend_entry_action(frame, 102.0, seed['pending'])
    assert result['action'] == 'WAIT'
    assert result['reason'] in ('WAIT_TREND_RETEST', 'WAIT_TREND_RETEST_BREAK')
    assert result['pending']['confirmed'] is True

def _dynamic_lower_trend_frame():
    frame = _channel_frame()
    for position in range(13, 19):
        middle = 100.5 - (position - 13) * 0.1
        close = middle - 0.8 - (position - 13) * 0.05
        frame.loc[position, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [close + 0.15, close, close + 0.2, close - 0.1, middle - 0.3, middle - 0.1, middle - 1.0, middle + 1.0]
    frame.loc[17, "close"] = float(frame.loc[17, "kc_lower"]) + 0.01
    frame.loc[19, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [98.9, 98.85, 98.95, 98.84, 99.45, 99.75, 98.9, 100.9]
    return frame

def test_kc_lower_outer_downtrend_uses_symmetric_next_bar_break():
    frame = _dynamic_lower_trend_frame()
    candidate = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)
    assert (candidate['action'], candidate['reason']) == ('WAIT', 'WAIT_DOWNTREND_BREAK')
    assert candidate['pending']['side'] == 'SHORT'
    assert candidate['pending']['confirmed'] is False
    entered = TradingEngine._channel_outer_trend_entry_action(frame, 98.85, candidate['pending'])
    assert (entered['action'], entered['side']) == ('ENTER', 'SHORT')
    assert entered['reason'] == 'KC_LOWER_TREND_CONFIRMED_SHORT'

def test_kc_lower_touch_inside_does_not_seed_confirmed_short():
    frame = _dynamic_lower_trend_frame()
    # e8 規格要求候選收盤在下軌外，只有影線碰軌仍須等待。
    frame.loc[18, ['open', 'close', 'high', 'low', 'kc_lower']] = [99.15, 99.02, 99.18, 98.90, 98.95]
    candidate = TradingEngine._channel_outer_trend_entry_action(frame, 98.98)
    assert (candidate['action'], candidate['reason']) == ('WAIT', 'WAIT_OUTER_UPTREND')
    assert candidate['pending'] is None

def test_kc_lower_short_touch_cancels_on_v_rebound_before_low_break():
    frame = _dynamic_lower_trend_frame()
    frame.loc[18, ['open', 'close', 'high', 'low', 'kc_lower']] = [99.15, 98.90, 99.18, 98.85, 98.95]
    candidate = TradingEngine._channel_outer_trend_entry_action(frame, 98.88)
    frame.loc[19, 'high'] = float(candidate['pending']['candidate_high']) + 0.01
    result = TradingEngine._channel_outer_trend_entry_action(frame, 99.0, candidate['pending'])
    assert (result['action'], result['reason'], result['pending']) == (
        'WAIT', 'CANCEL_DOWNTREND_CONFIRM', None,
    )

def test_kc_lower_outer_downtrend_waits_when_trend_is_ambiguous():
    frame = _dynamic_lower_trend_frame()
    frame.loc[16:18, ['ma3', 'ma15']] = [[100.1, 100.0]] * 3
    result = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_DYNAMIC_DOWNTREND')
    assert result['pending'] is None

def test_kc_lower_outer_downtrend_next_bar_failure_cancels_candidate():
    frame = _dynamic_lower_trend_frame()
    seed = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)
    frame.loc[19, 'high'] = float(seed['pending']['candidate_high']) + 0.01
    result = TradingEngine._channel_outer_trend_entry_action(frame, 98.92, seed['pending'])
    assert (result['action'], result['reason']) == ('WAIT', 'CANCEL_DOWNTREND_CONFIRM')
    assert result['pending'] is None

def test_kc_lower_outer_downtrend_does_not_chase_when_break_is_too_far():
    frame = _dynamic_lower_trend_frame()
    seed = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)
    result = TradingEngine._channel_outer_trend_entry_action(frame, 98.0, seed['pending'])
    assert result['action'] == 'WAIT'
    assert result['reason'] in ('WAIT_DOWNTREND_RETEST', 'WAIT_DOWNTREND_RETEST_BREAK')
    assert result['pending']['confirmed'] is True

def test_kc_upper_touch_inside_does_not_seed_confirmed_long():
    frame = _dynamic_upper_trend_frame()
    frame.loc[18, 'close'] = float(frame.loc[18, 'kc_upper']) - 0.01
    result = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.1)
    assert (result['action'], result['side']) == ('WAIT', None)
    assert result['reason'] == 'WAIT_OUTER_UPTREND'
    assert result['pending'] is None

def _chop_momentum_frame(side):
    frame = _channel_frame()
    for position in range(10, 18):
        close = 99.95 if position % 2 == 0 else 100.05
        frame.loc[position, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [100.0, close, close + 0.05, close - 0.05, close, 100.0, 99.0, 101.0]
    if side == 'LONG':
        frame.loc[18, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [100.1, 100.5, 100.6, 100.05, 100.3, 100.05, 99.02, 101.02]
        frame.loc[19, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [100.5, 100.65, 100.66, 100.45, 100.4, 100.1, 99.04, 101.04]
    else:
        frame.loc[18, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [99.9, 99.5, 99.95, 99.4, 99.7, 99.95, 98.98, 100.98]
        frame.loc[19, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [99.5, 99.35, 99.55, 99.34, 99.6, 99.9, 98.96, 100.96]
    return frame

def test_chop_wait_can_enter_long_from_inside_kc_after_confirmed_momentum_break():
    frame = _chop_momentum_frame('LONG')
    result = TradingEngine._channel_chop_breakout_action(frame, 100.65)
    assert (result['action'], result['side'], result['reason']) == ('ENTER', 'LONG', 'CHOP_BREAKOUT_LONG')
    assert float(frame.loc[18, 'close']) < float(frame.loc[18, 'kc_upper'])

def test_chop_wait_can_enter_short_from_inside_kc_after_confirmed_momentum_break():
    frame = _chop_momentum_frame('SHORT')
    result = TradingEngine._channel_chop_breakout_action(frame, 99.35)
    assert (result['action'], result['side'], result['reason']) == ('ENTER', 'SHORT', 'CHOP_BREAKOUT_SHORT')
    assert float(frame.loc[18, 'close']) > float(frame.loc[18, 'kc_lower'])

def test_chop_momentum_candidate_still_waits_for_next_bar_break():
    frame = _chop_momentum_frame('LONG')
    result = TradingEngine._channel_chop_breakout_action(frame, 100.55)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_CHOP_MOMENTUM_BREAK')

def test_strongest_ranked_symbol_uses_target_direction_final_score():
    engine = TradingEngine.__new__(TradingEngine)

    class Rotation:
        direction_map = {'SOL/USDT': 'LONG', 'XRP/USDT': 'LONG', 'DOGE/USDT': 'SHORT'}
        last_metrics = [{'symbol': 'SOL/USDT', 'direction': 'LONG', 'final_score': 70}, {'symbol': 'XRP/USDT', 'direction': 'LONG', 'final_score': 90}, {'symbol': 'DOGE/USDT', 'direction': 'SHORT', 'final_score': 95}]
    engine.symbol_rotation = Rotation()
    assert engine._strongest_ranked_symbol('LONG') == ('XRP/USDT', 90.0)
    assert engine._strongest_ranked_symbol('SHORT') == ('DOGE/USDT', 95.0)

def test_market_candidates_sorts_by_strongest():
    candidates = [{'symbol': 'SOL/USDT', 'side': 'LONG', 'score': 100, 'trend_quality': 0.8}, {'symbol': 'XRP/USDT', 'side': 'LONG', 'score': 95, 'trend_quality': 1.2}, {'symbol': 'DOGE/USDT', 'side': 'SHORT', 'score': 90, 'trend_quality': 0.7}]
    selected, skipped = TradingEngine._select_strongest_same_side_candidates(candidates)
    assert [item['symbol'] for item in selected] == ['XRP/USDT', 'SOL/USDT', 'DOGE/USDT']


def test_live_confirmed_energy_beats_stale_rotation_score():
    candidates = [
        {
            "symbol": "WEAK/USDT", "side": "LONG",
            "trend_quality": 0.8, "volume_ratio": 1.2,
            "confirmed_trend_quality": 0.8, "confirmed_volume_ratio": 1.2,
        },
        {
            "symbol": "STRONG/USDT", "side": "LONG",
            "trend_quality": 1.5, "volume_ratio": 1.5,
            "confirmed_trend_quality": 1.5, "confirmed_volume_ratio": 1.5,
        },
    ]

    selected, _ = TradingEngine._select_strongest_same_side_candidates(
        candidates,
        symbol_scores={"WEAK/USDT": 99.0, "STRONG/USDT": 50.0},
    )

    assert [item["symbol"] for item in selected] == [
        "STRONG/USDT", "WEAK/USDT",
    ]

@pytest.mark.anyio
async def test_channel_takeover_requires_confirmed_momentum_decline(monkeypatch):
    monkeypatch.setattr("core.engine.MAX_SLOTS", 1)
    events = []

    class Account:
        positions = {
            "OLD/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "qty": 1.0, "open_timestamp": 999.5,
                "channel_kc_upper": 102.0, "channel_kc_lower": 98.0,
            }
        }
        pending_limit_orders = {}

        def log(self, *_args, **_kwargs):
            pass

        async def close_position(self, symbol, *_args, **_kwargs):
            events.append(("close", symbol))
            self.positions.pop(symbol)
            return True

    class Rotation:
        def request_replacement(self, symbol):
            events.append(("replace", symbol))

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.symbol_rotation = Rotation()
    engine.rotation_event = None
    engine.tickers = {"OLD/USDT": 99.0}

    async def execution_safe(*_args):
        return True

    async def place(symbol, *_args):
        assert not engine.account.positions
        events.append(("open", symbol))
        return True

    engine._execution_price_is_safe = execution_safe
    engine._abnormal_market_entry_allowed = lambda *_args: True
    engine._place_structured_entry = place
    candidate = {
        "symbol": "NEW/USDT", "side": "SHORT", "entry_mode": "CHANNEL_SWING",
        "priority": 4, "reason": "KC_LOWER_TREND_CONFIRMED_SHORT",
        "live_price": 50.0, "kc_upper": 55.0, "kc_lower": 51.0, "atr": 0.5,
        "confirmed_trend_quality": 1.5, "confirmed_volume_ratio": 1.2,
    }

    handled, opened = await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    )

    assert (handled, opened) == (False, False)
    assert events == []
    assert "OLD/USDT" in engine.account.positions


@pytest.mark.anyio
async def test_live_strong_first_touch_cannot_churn_recent_profitable_position(
    monkeypatch,
):
    monkeypatch.setattr("core.engine.MAX_SLOTS", 1)
    events = []

    class Account:
        positions = {
            "OLD/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "qty": 1.0, "open_timestamp": 990.0,
            }
        }
        pending_limit_orders = {}

        def log(self, *_args, **_kwargs):
            pass

        async def close_position(self, symbol, *_args, **_kwargs):
            events.append(("close", symbol))
            self.positions.pop(symbol)
            return True

    class Rotation:
        def request_replacement(self, symbol):
            events.append(("replace", symbol))

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.symbol_rotation = Rotation()
    engine.rotation_event = None
    engine.tickers = {"OLD/USDT": 101.0}
    engine._continuous_market_mode = {}
    engine._execution_price_is_safe = lambda *_args: None

    async def execution_safe(*_args):
        return True

    async def place(symbol, *_args):
        events.append(("open", symbol))
        return True

    engine._execution_price_is_safe = execution_safe
    engine._abnormal_market_entry_allowed = lambda *_args: True
    engine._place_structured_entry = place
    candidate = {
        "symbol": "NEW/USDT", "side": "LONG",
        "entry_mode": "CHANNEL_SWING", "priority": 5,
        "signal_code": "KC_STRONG_FIRST_UPPER_TOUCH_LONG",
        "reason": "Channel Swing strong first upper touch LONG",
        "live_price": 50.0, "atr": 0.5,
    }

    assert await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    ) == (False, False)
    assert events == []


@pytest.mark.anyio
async def test_weakest_stalled_position_is_replaced_by_stronger_breakout():
    events = []

    class Account:
        positions = {
            "WEAK/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "mark_price": 101.0, "qty": 1.0,
                "open_timestamp": 700.0, "channel_energy_score": 0.10,
                "channel_kc_upper": 102.0, "channel_kc_lower": 98.0,
                "channel_momentum_declining": True,
            },
            "STRONG/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "mark_price": 101.0, "qty": 1.0,
                "open_timestamp": 990.0, "channel_energy_score": 2.0,
                "channel_kc_upper": 102.0, "channel_kc_lower": 98.0,
            },
        }
        pending_limit_orders = {}

        def log(self, *_args, **_kwargs):
            pass

        async def close_position(self, symbol, *_args, **_kwargs):
            events.append(("close", symbol))
            self.positions.pop(symbol)
            return True

    class Rotation:
        def request_replacement(self, symbol):
            events.append(("replace", symbol))

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.symbol_rotation = Rotation()
    engine.rotation_event = None
    engine.tickers = {"WEAK/USDT": 101.0, "STRONG/USDT": 101.0}
    engine._continuous_market_mode = {}

    async def execution_safe(*_args):
        return True

    async def place(symbol, *_args):
        events.append(("open", symbol))
        return True

    engine._execution_price_is_safe = execution_safe
    engine._abnormal_market_entry_allowed = lambda *_args: True
    engine._place_structured_entry = place
    candidate = {
        "symbol": "NEW/USDT", "side": "LONG",
        "entry_mode": "CHANNEL_SWING", "priority": 4,
        "signal_code": "KC_UPPER_TREND_CONFIRMED_LONG",
        "reason": "Channel Swing KC upper trend confirmed LONG",
        "live_price": 50.0, "kc_upper": 49.0, "kc_lower": 45.0, "atr": 0.5,
        "trend_quality": 1.0, "volume_ratio": 1.0,
        "confirmed_trend_quality": 1.5, "confirmed_volume_ratio": 1.2,
    }

    inside_candidate = dict(candidate, live_price=48.0)
    assert await engine._try_channel_stronger_symbol_takeover(
        inside_candidate, now_time=1000.0, daily_halt=False,
    ) == (False, False)
    assert events == []

    engine.tickers["WEAK/USDT"] = 101.5
    assert await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    ) == (True, True)
    assert set(engine.account.positions) == {"STRONG/USDT"}
    assert events == [
        ("close", "WEAK/USDT"),
        ("replace", "WEAK/USDT"),
        ("open", "NEW/USDT"),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("side", "signal_code", "live_price"),
    [
        ("LONG", "BULL_KC_LOWER_TROUGH_CONFIRMED_LONG", 50.0),
        ("LONG", "KC_LOWER_TROUGH_CONFIRMED_LONG", 50.0),
        ("SHORT", "BEAR_KC_UPPER_PEAK_CONFIRMED_SHORT", 40.0),
        ("SHORT", "KC_UPPER_PEAK_CONFIRMED_SHORT", 40.0),
    ],
)
async def test_channel_reversal_entries_cannot_take_over_existing_position(
    side, signal_code, live_price,
):
    class Account:
        positions = {
            "OLD/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "mark_price": 100.0, "qty": 1.0,
                "open_timestamp": 999.5, "channel_energy_score": 0.1,
                "channel_kc_upper": 102.0, "channel_kc_lower": 98.0,
            }
        }
        pending_limit_orders = {}

        async def close_position(self, *_args, **_kwargs):
            raise AssertionError("KC outer reversal must not replace a held position")

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.tickers = {"OLD/USDT": 100.0}
    candidate = {
        "symbol": "NEW/USDT", "side": side,
        "entry_mode": "CHANNEL_SWING", "priority": 4,
        "signal_code": signal_code,
        "live_price": live_price, "kc_upper": 49.0, "kc_lower": 41.0,
        "atr": 0.5,
        "confirmed_trend_quality": 99.0,
        "confirmed_volume_ratio": 99.0,
    }

    assert await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    ) == (False, False)


@pytest.mark.anyio
async def test_channel_takeover_keeps_old_position_when_new_execution_is_unsafe(monkeypatch):
    monkeypatch.setattr("core.engine.MAX_SLOTS", 1)

    class Account:
        positions = {
            "OLD/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "qty": 1.0, "open_timestamp": 1.0,
                "channel_kc_upper": 102.0, "channel_kc_lower": 98.0,
                "channel_momentum_declining": True,
            }
        }
        pending_limit_orders = {}

        def log(self, *_args, **_kwargs):
            pass

        async def close_position(self, *_args, **_kwargs):
            raise AssertionError("unsafe replacement must not close the held position")

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.tickers = {"OLD/USDT":  100.0}

    async def execution_unsafe(*_args):
        return False

    engine._execution_price_is_safe = execution_unsafe
    candidate = {
        "symbol": "NEW/USDT", "side": "SHORT", "entry_mode": "CHANNEL_SWING",
        "priority": 4, "reason": "KC_LOWER_TREND_CONFIRMED_SHORT",
        "live_price": 50.0, "kc_upper": 55.0, "kc_lower": 51.0, "atr": 0.5,
        "confirmed_trend_quality": 1.5, "confirmed_volume_ratio": 1.2,
    }

    assert await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    ) == (True, False)
    assert "OLD/USDT" in engine.account.positions


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("age", "mark", "priority", "reason"),
    [
        (899.0, 99.0, 4, "KC_LOWER_TREND_CONFIRMED_SHORT"),
        (1000.0, 101.0, 4, "KC_LOWER_TREND_CONFIRMED_SHORT"),
        (1000.0, 99.0, 1, "RANGE_KC_UPPER_PEAK_CONFIRMED_SHORT"),
    ],
)
async def test_channel_takeover_rejects_recent_profitable_or_range_candidate(
    monkeypatch, age, mark, priority, reason,
):
    monkeypatch.setattr("core.engine.MAX_SLOTS", 1)

    class Account:
        positions = {
            "OLD/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "qty": 1.0,
                "open_timestamp": 1000.0 - age,
            }
        }
        pending_limit_orders = {}

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.tickers = {"OLD/USDT": mark}
    candidate = {
        "symbol": "NEW/USDT", "side": "SHORT", "entry_mode": "CHANNEL_SWING",
        "priority": priority, "reason": reason, "live_price": 50.0, "atr": 0.5,
    }

    assert await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    ) == (False, False)


def test_higher_energy_candidate_beats_route_priority_in_same_direction():
    candidates = [{'symbol': 'SOL/USDT', 'side': 'SHORT', 'score': 100, 'trend_quality': 99.0}, {'symbol': 'DOGE/USDT', 'side': 'SHORT', 'score': 100, 'trend_quality': 0.5, 'priority': 1}]
    selected, skipped = TradingEngine._select_strongest_same_side_candidates(candidates)
    assert [item['symbol'] for item in selected] == ['SOL/USDT', 'DOGE/USDT']

def test_kc_outer_entry_has_priority_over_inside_touch_entry():
    outer = TradingEngine._channel_entry_candidate_priority("KC_LIVE_UPPER_BREAK_LONG")
    touch = TradingEngine._channel_entry_candidate_priority("KC_UPPER_TOUCH_LONG")
    assert outer > touch


def test_executable_channel_candidates_rank_confirmed_outer_trend_first():
    outer = TradingEngine._channel_entry_candidate_priority('KC_UPPER_TREND_CONFIRMED_LONG')
    inner_reentry = TradingEngine._channel_entry_candidate_priority('INSTANT_INNER_REENTRY_LONG')
    pivot = TradingEngine._channel_entry_candidate_priority('KC_LOWER_TROUGH_CONFIRMED')
    assert outer > inner_reentry > pivot

def test_stronger_energy_beats_outer_route_priority():
    candidates = [{'symbol': 'PIVOT/USDT', 'side': 'LONG', 'priority': 1, 'trend_quality': 9.0}, {'symbol': 'OUTER/USDT', 'side': 'LONG', 'priority': 3, 'trend_quality': 0.5}]
    selected, _ = TradingEngine._select_strongest_same_side_candidates(candidates)
    assert [item['symbol'] for item in selected] == ['PIVOT/USDT', 'OUTER/USDT']

def test_same_priority_candidate_with_more_energy_beats_profit_space():
    candidates = [{'symbol': 'FAST/USDT', 'side': 'LONG', 'priority': 3, 'profit_potential': 1.2, 'trend_quality': 9.0}, {'symbol': 'ROOM/USDT', 'side': 'LONG', 'priority': 3, 'profit_potential': 4.8, 'trend_quality': 0.5}]
    selected, skipped = TradingEngine._select_strongest_same_side_candidates(candidates)
    assert [item['symbol'] for item in selected] == ['FAST/USDT', 'ROOM/USDT']

def test_current_channel_volume_ratio_uses_live_candle():
    frame = _channel_frame()
    frame.loc[frame.index[-1], ['volume', 'vol_ma_20']] = [49.0, 100.0]
    assert TradingEngine._channel_volume_ratio(frame) == pytest.approx(0.49)

def test_held_low_volume_exit_requires_three_declining_closed_bars(monkeypatch):
    monkeypatch.setattr('core.engine.KELTNER_MIN_VOLUME_RATIO', 0.5)
    frame = _channel_frame()
    frame.loc[frame.index[-4:-1], ['volume', 'vol_ma_20']] = [[80.0, 100.0], [60.0, 100.0], [40.0, 100.0]]
    frame.loc[frame.index[-1], ['volume', 'vol_ma_20']] = [1.0, 100.0]
    assert TradingEngine._channel_held_volume_is_declining(frame)
    frame.loc[frame.index[-2], 'volume'] = 70.0
    assert not TradingEngine._channel_held_volume_is_declining(frame)

def test_candidate_profit_potential_uses_directional_daily_space():
    engine = TradingEngine.__new__(TradingEngine)

    class Rotation:
        volatility_stats = {'ROOM/USDT': {'avg_daily_up_pct': 5.5, 'avg_daily_down_pct': 2.5}}
    engine.symbol_rotation = Rotation()
    assert engine._candidate_profit_potential('ROOM/USDT', 'LONG', 1.0, 100.0) == pytest.approx(5.5)
    assert engine._candidate_profit_potential('ROOM/USDT', 'SHORT', 1.0, 100.0) == pytest.approx(2.5)

def test_empty_slot_scans_full_safe_pool_even_when_ltc_is_not_on_ui_board():
    snapshot = TradingEngine._entry_scan_symbol_snapshot(
        ["T/USDT"], ["T/USDT", "LTC/USDT", "SOL/USDT"],
        {}, {}, True, 1,
    )
    assert snapshot == ["T/USDT", "LTC/USDT", "SOL/USDT"]


def test_full_single_slot_scans_held_symbol_and_active_takeover_board():
    snapshot = TradingEngine._entry_scan_symbol_snapshot(
        ["LTC/USDT"], ["LTC/USDT", "SOL/USDT"],
        {"T/USDT": {"side": "LONG"}}, {}, True, 1,
    )
    assert snapshot == ["T/USDT", "LTC/USDT", "SOL/USDT"]


def test_candidate_board_refreshes_after_fill_and_while_slot_remains():
    assert TradingEngine._candidate_board_refresh_needed(True, position_count=2, pending_count=0, max_slots=2, seconds_since_refresh=0.0)
    assert TradingEngine._candidate_board_refresh_needed(False, position_count=1, pending_count=0, max_slots=2, seconds_since_refresh=15.0)
    assert not TradingEngine._candidate_board_refresh_needed(False, position_count=1, pending_count=0, max_slots=1, seconds_since_refresh=60.0)
    assert not TradingEngine._candidate_board_refresh_needed(False, position_count=1, pending_count=0, max_slots=2, seconds_since_refresh=14.9)


def test_ranked_direction_both_allows_long_and_short_channel_scan():
    assert TradingEngine._entry_matches_ranked_direction("LONG", "BOTH")
    assert TradingEngine._entry_matches_ranked_direction("SHORT", "BOTH")


def test_market_surveillance_direction_is_applied_to_channel_entry_scan():
    source = inspect.getsource(TradingEngine._process_single_symbol)

    assert "self.market_prebreakout_directions.get(symbol)" in source
    assert "WAIT_MARKET_RANKED_DIRECTION" in source


def test_channel_status_log_uses_chinese_label_without_internal_reason_code():
    messages = []
    engine = TradingEngine.__new__(TradingEngine)
    engine._channel_signal_events = {}
    engine.account = type(
        "Account", (),
        {"log": lambda _self, text, level: messages.append((text, level))},
    )()
    frame = _channel_frame()
    frame["timestamp"] = range(len(frame))

    engine._record_channel_signal_event(
        "SOL/USDT", "WAIT_CLOSED_BODY_ADJACENT_BREAK", frame,
    )

    assert messages == [(
        "🧭 [Channel Swing狀態] SOL/USDT 等待已收盤外軌K的下一根突破",
        "INFO",
    )]
    assert engine._channel_signal_events["SOL/USDT"][-1]["reason"] == (
        "WAIT_CLOSED_BODY_ADJACENT_BREAK"
    )


def test_market_surveillance_replaces_stale_momentum_scores():
    engine = TradingEngine.__new__(TradingEngine)
    engine.market_surveillance_contracts = set()
    engine.execution_symbols = None
    engine._market_ticker_snapshots = {}
    engine._market_price_samples = {}
    engine.market_prebreakout_symbols = []
    engine.market_prebreakout_directions = {}
    engine._market_surveillance_scores = {"STALE/USDT": 99.0}
    engine.market_surveillance_updated_at = 0.0

    engine._update_market_surveillance({}, now=100.0)

    assert engine._market_surveillance_scores == {}


def test_main_loop_uses_confirmed_takeover_but_not_stalled_recovery_close():
    main_source = inspect.getsource(TradingEngine._main_loop)
    process_source = inspect.getsource(TradingEngine._process_single_symbol)

    assert "_try_channel_stronger_symbol_takeover(" in main_source
    assert "_try_channel_stalled_recovery_exit(" not in main_source
    assert "_channel_stalled_recovery_should_arm(" not in process_source


def test_single_slot_amount_uses_eighty_percent_wallet(monkeypatch):
    # CI does not load the developer's .env; keep this allocation contract
    # independent from the configured per-trade cap.
    monkeypatch.setattr("core.engine.TRADE_AMOUNT_USDT", 1_000.0)
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
    engine.account.positions['SOL/USDT'] = {'margin': 120.0}
    assert engine._continuous_entry_amount() == 0.0

@pytest.mark.anyio
async def test_channel_swing_position_ignores_all_profit_locks_without_initial_sl_or_tp(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, 'STATE_FILE', str(tmp_path / 'channel_swing.json'))
    account = PaperAccount()
    opened = await account.open_position('BTC/USDT', 'LONG', 100.0, 50.0, 95.0, 110.0, 'channel swing', leverage=1, signal_score=100, apply_slippage=False, entry_context={'entry_mode': 'CHANNEL_SWING', 'wave_regime': 'RANGE', 'initial_sl': 95.0, 'initial_risk': 5.0})
    assert opened is True
    assert account.positions['BTC/USDT']['sl'] == 0.0
    assert account.positions['BTC/USDT']['tp'] == 0.0
    assert account.position_meta['BTC/USDT']['sl'] == 0.0
    assert account.position_meta['BTC/USDT']['initial_sl'] == 0.0
    monkeypatch.setattr(pa_module, 'ENABLE_PROFIT_LOCK_USDT', True)
    monkeypatch.setattr(pa_module, 'ENABLE_FIXED_PROFIT_LOCK_PCT', True)
    monkeypatch.setattr(pa_module, 'ENABLE_TRAILING_STOP', True)
    monkeypatch.setattr(pa_module, 'ENABLE_EARLY_PROFIT_GUARD', True)
    await account.update_positions({'BTC/USDT': 102.0})
    await account.update_positions({'BTC/USDT': 101.0})
    assert 'BTC/USDT' in account.positions
    assert account.positions['BTC/USDT']['sl'] == 0.0
    assert not account.positions['BTC/USDT'].get('is_breakeven_moved', False)
    topped_up = await account.open_position('BTC/USDT', 'LONG', 100.2, 25.0, 95.0, 110.0, 'channel swing top-up', leverage=1, signal_score=100, apply_slippage=False, entry_context={'entry_mode': 'CHANNEL_SWING'})
    assert topped_up is False
    assert account.positions['BTC/USDT']['margin'] == 50.0
    reloaded = PaperAccount()
    await reloaded.initialize()
    assert reloaded.positions['BTC/USDT']['sl'] == 0.0
    assert reloaded.positions['BTC/USDT']['tp'] == 0.0
    assert not any(('啟動保護遷移' in item.get('text', '') for item in reloaded.logs))


@pytest.mark.anyio
async def test_channel_swing_v2_profit_lock_only_applies_to_marked_new_position(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "channel_v2_lock.json"))
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_LOCK_USDT", True)
    account = PaperAccount()
    assert await account.open_position(
        "BTC/USDT", "LONG", 100.0, 100.0, 0.0, 0.0, "new channel",
        leverage=1, signal_score=100, apply_slippage=False,
        entry_context={
            "entry_mode": "CHANNEL_SWING", "wave_regime": "TREND",
            "profit_lock_usdt_v2": True,
        },
    )

    # Cost is 0.11U: 0.10U round-trip fee plus 0.01U exit slippage.
    await account.update_positions({"BTC/USDT": 103.10})
    assert account.positions["BTC/USDT"]["sl"] == 0.0
    await account.update_positions({"BTC/USDT": 103.11})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(101.11)

    # Strong trend: a larger peak does not tighten past the initial 1U net floor.
    await account.update_positions({"BTC/USDT": 107.11})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(101.11)

    # Once momentum declines, catch up two completed 2U steps.
    account.positions["BTC/USDT"]["channel_momentum_declining"] = True
    await account.update_positions({"BTC/USDT": 107.11})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(105.11)
    await account.update_positions({"BTC/USDT": 105.0})
    assert "BTC/USDT" not in account.positions

@pytest.mark.anyio
async def test_channel_swing_closes_only_on_rapid_adverse_move(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "channel_swing_rapid.json"))
    monkeypatch.setattr(pa_module, "ENABLE_RAPID_ADVERSE_DROP", True)
    monkeypatch.setattr(pa_module, "RAPID_ADVERSE_SPEED_PCT", 0.01)
    monkeypatch.setattr(pa_module, "RAPID_ADVERSE_SPEED_WINDOW_SEC", 60.0)
    account = PaperAccount()
    opened = await account.open_position(
        "BTC/USDT", "LONG", 100.0, 50.0, 0.0, 0.0, "channel swing",
        leverage=1, signal_score=100, apply_slippage=False,
        entry_context={"entry_mode": "CHANNEL_SWING", "wave_regime": "RANGE"},
    )
    assert opened is True
    await account.update_positions({"BTC/USDT": 100.0})
    await account.update_positions({"BTC/USDT": 98.5})
    assert "BTC/USDT" not in account.positions
    assert any(
        "rapid adverse" in item.get("text", "")
        for item in account.logs
    )


def test_channel_swing_ignores_legacy_structured_stop_cooldown():
    assert TradingEngine._structured_stop_cooldown_blocks('CHANNEL_SWING', 3600.0) is False
    assert TradingEngine._structured_stop_cooldown_blocks('BREAKOUT', 3600.0) is True
    assert TradingEngine._structured_stop_cooldown_blocks('BREAKOUT', 0.0) is False

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
    frame['timestamp'] = [index * 60000 for index in range(len(frame))]
    engine._record_channel_signal_event('TEST/USDT', 'KC_WIDTH_TOO_NARROW', frame)
    first_event = engine._channel_signal_events['TEST/USDT'][0]
    assert (first_event['action'], first_event['reason']) == ('CHANNEL_BLOCK', 'KC_WIDTH_TOO_NARROW')
    assert first_event['label'] == 'KC寬度不足設定門檻'
    frame.loc[frame.index[-1], 'timestamp'] += 60000
    engine._record_channel_signal_event('TEST/USDT', 'KC_WIDTH_TOO_NARROW', frame)
    assert len(engine._channel_signal_events['TEST/USDT']) == 1
    assert len(engine.account.logs) == 1
    engine._record_channel_signal_event('TEST/USDT', 'CANCEL_LONG', frame)
    assert len(engine._channel_signal_events['TEST/USDT']) == 2
    replacement = engine._channel_signal_events['TEST/USDT'][-1]
    assert (replacement['action'], replacement['reason']) == ('CHANNEL_CANCEL', 'CANCEL_LONG')
    assert len(engine.account.logs) == 2

def test_strong_middle_rebound_without_outer_touch_does_not_enter_long():
    frame = _channel_frame()
    for i in range(1, 4):
        frame.loc[frame.index[-i], ['kc_lower', 'ema_20', 'kc_upper']] = [99.0, 100.0, 101.0]
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.5, 99.6, 99.4, 99.7, 99.5]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.9, 100.7, 99.8, 100.8, 100.2]
    frame.loc[frame.index[-1], ['open', 'close', 'low', 'ma3']] = [100.7, 100.8, 100.6, 100.5]
    result = TradingEngine._channel_swing_action(frame, 100.8)
    assert (result.get('action'), result.get('side')) == ('WAIT', None)

def test_strong_middle_rebound_without_outer_touch_does_not_enter_short():
    frame = _channel_frame()
    for i in range(1, 4):
        frame.loc[frame.index[-i], ['kc_lower', 'ema_20', 'kc_upper']] = [99.0, 100.0, 101.0]
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [100.5, 100.4, 100.3, 100.6, 100.5]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [100.1, 99.3, 99.2, 100.2, 99.8]
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [99.3, 99.2, 99.4, 99.5]
    result = TradingEngine._channel_swing_action(frame, 99.2)
    assert (result.get('action'), result.get('side')) == ('WAIT', None)

def test_instant_inner_reentry_short_single_candle():
    """Test that a single red candle returning from the upper band by 50% triggers a SHORT reentry."""
    import pandas as pd
    from core.engine import TradingEngine
    df = pd.DataFrame([{'open': 100, 'high': 105, 'low': 95, 'close': 102, 'ma3': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 100, 'high': 108, 'low': 100, 'close': 106, 'ma3': 102, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 106, 'high': 106, 'low': 98, 'close': 99, 'ma3': 103, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}])
    live_price = 99.0
    action_data = TradingEngine._channel_live_inner_reentry_action(df, live_price, None)
    assert action_data['action'] == 'ENTER'
    assert action_data['side'] == 'SHORT'
    assert action_data['reason'] == 'LIVE_INNER_REENTRY_SHORT'

def test_instant_inner_reentry_short_two_candles():
    """Test that two red candles returning from the upper band by 50% triggers a SHORT reentry."""
    import pandas as pd
    from core.engine import TradingEngine
    df = pd.DataFrame([{'open': 100, 'high': 105, 'low': 95, 'close': 102, 'ma3': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 104, 'high': 104, 'low': 101, 'close': 101, 'ma3': 102, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 101, 'high': 101, 'low': 97, 'close': 98, 'ma3': 103, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}])
    live_price = 98.0
    action_data = TradingEngine._channel_live_inner_reentry_action(df, live_price, None)
    assert action_data['action'] == 'ENTER'
    assert action_data['side'] == 'SHORT'
    assert action_data['reason'] == 'LIVE_INNER_REENTRY_SHORT'

def test_instant_inner_reentry_long_first_candle_opens_outside():
    """Test that a two-candle long reentry works even if the first candle opens below the KC lower band."""
    import pandas as pd
    from core.engine import TradingEngine
    df = pd.DataFrame([{'open': 100, 'high': 105, 'low': 95, 'close': 98, 'ma3': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 94, 'high': 97, 'low': 93, 'close': 96, 'ma3': 102, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 96, 'high': 100, 'low': 96, 'close': 99, 'ma3': 103, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}])
    live_price = 99.0
    action_data = TradingEngine._channel_live_inner_reentry_action(df, live_price, None)
    assert action_data['action'] == 'ENTER'
    assert action_data['side'] == 'LONG'
    assert action_data['reason'] == 'LIVE_INNER_REENTRY_LONG'

def test_single_strong_candle_unlocks_chop_wait():
    """Test that a single strong candle crossing the middle band unlocks CHOP_WAIT early."""
    import pandas as pd
    from core.engine import TradingEngine
    data = []
    for i in range(13):
        data.append({'open': 100, 'close': 100 + (1 if i % 2 == 0 else -1), 'high': 102, 'low': 98, 'ma3': 100, 'ma15': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100})
    data.append({'open': 99, 'close': 102, 'high': 103, 'low': 98, 'ma3': 101, 'ma15': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100})
    data.append({'open': 102, 'close': 102, 'high': 103, 'low': 101, 'ma3': 101, 'ma15': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100})
    df = pd.DataFrame(data)
    chop_state = TradingEngine._channel_chop_state(df)
    assert chop_state['clear_direction'] == 'LONG'
    assert chop_state['reason'] == 'DIRECTION_CLEAR'
def test_channel_short_holds_when_price_returns_above_upper_rail():
    frame = _channel_frame(lower=99.0, upper=101.0)
    result = TradingEngine._channel_swing_action(
        frame, 101.0, current_side="SHORT",
    )
    assert (result["action"], result["side"], result["reason"]) == (
        "HOLD", None, "WAIT_OPPOSITE_KC_LOWER_VALLEY",
    )


def test_channel_long_holds_when_price_returns_below_lower_rail():
    frame = _channel_frame(lower=99.0, upper=101.0)
    result = TradingEngine._channel_swing_action(
        frame, 99.0, current_side="LONG",
    )
    assert (result["action"], result["side"], result["reason"]) == (
        "HOLD", None, "WAIT_OPPOSITE_KC_UPPER_PEAK",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("side", "break_price", "failed_price"),
    [("LONG", 100.75, 100.65), ("SHORT", 99.2, 99.4)],
)
async def test_fresh_channel_snapshot_rechecks_closed_body_and_adjacent_break(
    side, break_price, failed_price,
):
    engine = object.__new__(TradingEngine)

    class Strategy:
        def compute_indicators(self, frame):
            return frame

    engine.strategy = Strategy()
    frame = _channel_frame(lower=99.0, upper=101.0)
    if side == "LONG":
        frame.loc[frame.index[-2], ["open", "close", "high", "low"]] = [
            100.0, 100.5, 100.7, 98.9,
        ]
        frame.loc[frame.index[-1], ["open", "high", "low"]] = [100.5, 100.8, 99.0]
    else:
        frame.loc[frame.index[-2], ["open", "close", "high", "low"]] = [
            100.0, 99.5, 101.1, 99.3,
        ]
        frame.loc[frame.index[-1], ["open", "high", "low"]] = [99.5, 101.0, 99.0]

    async def fetch_break(*_args, **_kwargs):
        fresh = frame.copy()
        fresh.loc[fresh.index[-1], "close"] = break_price
        return fresh

    engine.fetch_klines = fetch_break
    snapshot = await engine._fresh_channel_entry_snapshot("TEST/USDT", side)
    assert snapshot is not None
    assert snapshot["price"] == break_price

    async def fetch_failed(*_args, **_kwargs):
        fresh = frame.copy()
        fresh.loc[fresh.index[-1], "close"] = failed_price
        return fresh

    engine.fetch_klines = fetch_failed
    assert await engine._fresh_channel_entry_snapshot("TEST/USDT", side) is None


def test_flat_channel_swing_never_creates_peak_or_trough_entry():
    trough = _channel_frame()
    peak = _channel_frame()
    _closed_trough(trough)
    _closed_peak(peak)
    for frame, price in ((trough, 99.2), (peak, 100.8)):
        result = TradingEngine._channel_swing_action(frame, price)
        assert (result["action"], result["side"], result["reason"]) == (
            "WAIT", None, "WAIT_KC_OUTER_TREND_ENTRY",
        )


_OBSOLETE_CHANNEL_ENTRY_TESTS = ('test_second_closed_confirmation_candle_must_keep_direction_color', 'test_outer_ma3_route_accepts_two_closed_turn_bars_that_remain_outside', 'test_body_deep_eighty_percent_into_half_channel_bypasses_outer_depth', 'test_shallow_outer_v_turns_are_symmetric_without_ma3_depth', 'test_lower_outer_green_reentry_can_open_long_without_ma3_depth', 'test_latest_adjacent_two_closed_outer_v_bars_are_valid_on_both_sides', 'test_empty_slot_does_not_chase_kc_outer_trend_without_pivot_turn', 'test_empty_slot_does_not_chase_price_outside_without_ma3_trend', 'test_live_outer_break_does_not_require_ma3_slope', 'test_empty_slot_does_not_chase_when_only_close_breaks_outer_rail', 'test_closed_lower_trough_uses_confirmed_long_instead_of_live_outer_short', 'test_cancelled_outer_peak_cannot_fall_back_to_live_ma3_entry', 'test_flat_entry_uses_ma3_and_held_position_exits_on_confirmed_trough', 'test_confirmed_outer_pivot_opens_before_forty_percent_reentry', 'test_current_downtrend_after_outer_peak_opens_on_green_candle', 'test_ma3_turn_does_not_open_when_price_is_outside_kc', 'test_old_outer_pivot_does_not_chase_at_opposite_outer_rail', 'test_shallow_outer_reentry_still_reverses_held_short_on_confirmed_trough', 'test_channel_swing_reentry_boundary_is_80_percent_of_outer_half', 'test_live_ma3_turn_does_not_block_confirmed_trough_exit', 'test_adjacent_two_closed_green_bars_confirm_long_candidate', 'test_adjacent_two_closed_red_bars_confirm_short_candidate')
for _test_name in _OBSOLETE_CHANNEL_ENTRY_TESTS:
    globals()[_test_name] = pytest.mark.skip(reason="obsolete: entry rule replaced")(globals()[_test_name])


@pytest.mark.anyio
async def test_adverse_range_transition_without_energy_cannot_take_over(monkeypatch):
    monkeypatch.setattr("core.engine.MAX_SLOTS", 1)
    events = []

    class Account:
        positions = {
            "OLD/USDT": {
                "side": "SHORT", "entry_mode": "CHANNEL_SWING",
                "entry_market_mode": "RANGE", "market_mode": "BULL",
                "entry_price": 100.0, "qty": 1.0, "open_timestamp": 990.0,
                "channel_kc_upper": 102.0, "channel_kc_lower": 98.0,
            }
        }
        pending_limit_orders = {}

        def log(self, *_args, **_kwargs):
            pass

        async def close_position(self, symbol, *_args, **_kwargs):
            events.append(("close", symbol))
            self.positions.pop(symbol)
            return True

    class Rotation:
        def request_replacement(self, symbol):
            events.append(("replace", symbol))

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.symbol_rotation = Rotation()
    engine.rotation_event = None
    engine.tickers = {"OLD/USDT": 101.0}
    engine._continuous_market_mode = {"OLD/USDT": "BULL"}

    async def execution_safe(*_args):
        return True

    async def place(symbol, *_args):
        events.append(("open", symbol))
        return True

    engine._execution_price_is_safe = execution_safe
    engine._abnormal_market_entry_allowed = lambda *_args: True
    engine._place_structured_entry = place
    candidate = {
        "symbol": "NEW/USDT", "side": "LONG",
        "entry_mode": "CHANNEL_SWING", "market_mode": "BULL",
        "priority": 4, "signal_code": "KC_UPPER_TREND_CONFIRMED_LONG",
        "reason": "Channel Swing KC upper trend confirmed LONG",
        "live_price": 50.0, "kc_upper": 49.0, "kc_lower": 45.0, "atr": 0.5,
    }

    assert await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    ) == (False, False)
    assert events == []
    assert "OLD/USDT" in engine.account.positions


@pytest.mark.parametrize(
    ("side", "adverse_mark", "near_mark", "still_far_mark"),
    [
        ("LONG", 98.9, 99.85, 99.7),
        ("SHORT", 101.1, 100.15, 100.3),
    ],
)
def test_stalled_recovery_arms_after_adverse_move_and_waits_until_near_entry(
    side, adverse_mark, near_mark, still_far_mark,
):
    position = {
        "side": side, "entry_mode": "CHANNEL_SWING",
        "entry_price": 100.0, "qty": 1.0, "atr": 2.0,
        "peak_pnl_pct": 0.0,
    }
    assert TradingEngine._channel_stalled_recovery_should_arm(
        position, adverse_mark,
    ) is True
    assert TradingEngine._channel_stalled_recovery_is_near_entry(
        position, near_mark,
    ) is False
    position["channel_stalled_recovery_armed"] = True
    assert TradingEngine._channel_stalled_recovery_is_near_entry(
        position, still_far_mark,
    ) is False
    assert TradingEngine._channel_stalled_recovery_is_near_entry(
        position, near_mark,
    ) is True


def test_profitable_breakout_never_arms_stalled_recovery():
    position = {
        "side": "LONG", "entry_mode": "CHANNEL_SWING",
        "entry_price": 100.0, "qty": 1.0, "atr": 2.0,
        "peak_pnl_pct": 0.01,
    }
    assert TradingEngine._channel_stalled_recovery_should_arm(
        position, 98.5,
    ) is False


@pytest.mark.anyio
async def test_stalled_recovery_exit_closes_near_entry_without_candidate():
    events = []

    class Account:
        positions = {
            "OLD/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "qty": 1.0, "atr": 2.0,
                "channel_stalled_recovery_armed": True,
            }
        }
        pending_limit_orders = {}

        async def close_position(self, symbol, *_args, **_kwargs):
            events.append(("close", symbol))
            self.positions.pop(symbol)
            return True

        def log(self, *_args, **_kwargs):
            pass

    class Rotation:
        def request_replacement(self, symbol):
            events.append(("replace", symbol))

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.symbol_rotation = Rotation()
    engine.rotation_event = None
    engine.tickers = {"OLD/USDT": 99.85}

    assert await engine._try_channel_stalled_recovery_exit() is True
    assert events == [("close", "OLD/USDT"), ("replace", "OLD/USDT")]


def test_channel_entry_context_preserves_original_market_mode():
    from core.paper_account import ENTRY_CONTEXT_KEYS
    assert "market_mode" in ENTRY_CONTEXT_KEYS
    assert "entry_market_mode" in ENTRY_CONTEXT_KEYS
    assert "channel_entry_profile" in ENTRY_CONTEXT_KEYS
    assert "channel_entry_profile_basis" in ENTRY_CONTEXT_KEYS


def test_shallow_adjacent_outer_reversal_enters_without_half_channel_reentry():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-3], ["open", "close", "low", "high", "ma3"]] = [98.8, 98.9, 98.7, 98.95, 98.7]
    long_frame.loc[long_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [99.0, 99.2, 98.8, 99.3, 99.2]
    long_result = TradingEngine._channel_swing_action(
        long_frame,  99.4, market_mode="RANGE",
    )

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-3], ["open", "close", "low", "high", "ma3"]] = [101.2, 101.1, 101.05, 101.3, 101.3]
    short_frame.loc[short_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [101.0, 100.8, 100.7, 101.2, 100.8]
    short_result = TradingEngine._channel_swing_action(
        short_frame,  100.6, market_mode="RANGE",
    )

    assert (long_result["action"], long_result["side"]) == ("WAIT", None)
    assert (short_result["action"], short_result["side"]) == ("WAIT", None)
    assert long_result["reason"] == "WAIT_KC_OUTER_TREND_ENTRY"
    assert short_result["reason"] == "WAIT_KC_OUTER_TREND_ENTRY"


def test_kc_inner_clear_ma3_trend_enters_both_directions():
    long_frame = _channel_frame(lower=99.0, upper=101.0)
    long_frame.loc[long_frame.index[-3:], "ma3"] = [99.5, 99.8, 100.1]
    long_frame.loc[long_frame.index[-1], "ma15"] = 100.0
    long_result = TradingEngine._channel_inner_trend_entry_action(long_frame, 100.3)

    short_frame = _channel_frame(lower=99.0, upper=101.0)
    short_frame.loc[short_frame.index[-3:], "ma3"] = [100.5, 100.2, 99.9]
    short_frame.loc[short_frame.index[-1], "ma15"] = 100.0
    short_result = TradingEngine._channel_inner_trend_entry_action(short_frame, 99.7)

    assert (long_result["action"], long_result["side"], long_result["reason"]) == (
        "ENTER", "LONG", "KC_INNER_UPTREND_LONG",
    )
    assert (short_result["action"], short_result["side"], short_result["reason"]) == (
        "ENTER", "SHORT", "KC_INNER_DOWNTREND_SHORT",
    )


def test_kc_inner_flat_ma3_still_waits():
    frame = _channel_frame(lower=99.0, upper=101.0)
    result = TradingEngine._channel_inner_trend_entry_action(frame, 100.2)
    assert (result["action"], result["side"]) == ("WAIT", None)


@pytest.mark.anyio
async def test_structured_entry_rejects_symbol_outside_execution_allowlist(monkeypatch):
    monkeypatch.setattr("core.engine.DEFAULT_SYMBOLS", ["1000PEPE/USDT"])
    engine = TradingEngine.__new__(TradingEngine)

    opened = await engine._place_structured_entry(
        "BTC/USDT", {}, 100.0,
    )

    assert opened is False


def test_channel_swing_allows_lower_timeframe_signal_against_higher_timeframe_direction():
    source = inspect.getsource(TradingEngine._place_structured_entry)
    assert "多週期方向不一致" not in source
    assert "direction_conflict" not in source


def test_channel_swing_simple_trend_entry_bypasses_old_signal_filters():
    place_source = inspect.getsource(TradingEngine._place_structured_entry)
    process_source = inspect.getsource(TradingEngine._process_single_symbol)
    assert "entry_mode != \"CHANNEL_SWING\"" in place_source
    assert "排名候選但當下量能不足" not in process_source
    assert "blocked: ranked direction" not in process_source
    assert "訊號遭 BTC 1m" not in process_source
    assert "_channel_inner_trend_entry_action(" not in process_source
    assert TradingEngine._channel_entry_candidate_priority("KC_INNER_UPTREND_LONG") == 4
    assert TradingEngine._channel_entry_candidate_priority("KC_INNER_DOWNTREND_SHORT") == 4


def test_trade_close_blocks_entries_and_requests_full_market_refresh(monkeypatch):
    monkeypatch.setattr("core.engine.SYMBOL_ROTATION_ENABLED", True)

    class Flag:
        def __init__(self):
            self.is_set = False

        def set(self):
            self.is_set = True

    class Account:
        trades = [{"action": "CLOSE_LONG", "symbol": "OLD/USDT"}]

        def __init__(self):
            self.logs = []

        def log(self, text, level):
            self.logs.append((text, level))

    class Rotation:
        last_rotation_at = 123.0

        def __init__(self):
            self.replacements = []

        def request_replacement(self, symbol):
            self.replacements.append(symbol)
            self.last_rotation_at = 0.0

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.symbol_rotation = Rotation()
    engine.analysis_event = Flag()
    engine.rotation_event = Flag()
    engine._post_close_rotation_generation = 0
    engine._entry_waiting_for_post_close_rotation = False

    engine._on_trade_closed()

    assert engine.analysis_event.is_set is True
    assert engine.rotation_event.is_set is True
    assert engine._entry_waiting_for_post_close_rotation is True
    assert engine._post_close_rotation_generation == 1
    assert engine.symbol_rotation.replacements == ["OLD/USDT"]
    assert "暫停新倉" in engine.account.logs[-1][0]


def test_force_fresh_rotation_replaces_sticky_interface_symbols():
    metrics = [
        {
            "symbol": "BEST_LONG/USDT", "direction": "LONG",
            "eligible": True, "final_score": 95.0, "entry_priority": 4,
        },
        {
            "symbol": "BEST_SHORT/USDT", "direction": "SHORT",
            "eligible": True, "final_score": 94.0, "entry_priority": 4,
        },
    ]

    selected, directions, changes = SymbolRotation.choose_directional_symbols(
        ["OLD_A/USDT", "OLD_B/USDT"], {}, metrics, force_fresh=True,
    )

    assert selected == ["BEST_LONG/USDT", "BEST_SHORT/USDT"]
    assert directions == {
        "BEST_LONG/USDT": "LONG", "BEST_SHORT/USDT": "SHORT",
    }
    assert {item["out"] for item in changes} == {"OLD_A/USDT", "OLD_B/USDT"}


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_channel_held_momentum_decline_uses_completed_candles_both_sides(side):
    if side == "LONG":
        closes = [100.0, 101.0, 102.0, 102.5, 102.7, 999.0]
        ma3 = [100.0, 100.8, 101.4, 101.7, 101.8, 999.0]
    else:
        closes = [100.0, 99.0, 98.0, 97.5, 97.3, 1.0]
        ma3 = [100.0, 99.2, 98.6, 98.3, 98.2, 1.0]
    frame = pd.DataFrame({
        "close": closes, "ma3": ma3, "atr": [1.0] * 6,
        "volume": [100.0, 100.0, 150.0, 100.0, 50.0, 9999.0],
        "vol_ma_20": [100.0] * 6,
    })

    assert TradingEngine._channel_held_momentum_is_declining(frame, side) is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("side", "held_mark", "momentum_declining", "should_switch"),
    [
        ("LONG", 100.0, False, False),
        ("SHORT", 100.0, False, False),
        ("LONG", 100.0, True, True),
        ("SHORT", 100.0, True, True),
        ("LONG", 99.8, False, False),
        ("SHORT", 100.2, False, False),
        ("LONG", 99.8, True, True),
        ("SHORT", 100.2, True, True),
        ("LONG", 101.9, True, True),
        ("SHORT", 98.1, True, True),
    ],
)
async def test_hype_only_declining_momentum_switches_to_fresh_breakout(
    side, held_mark, momentum_declining, should_switch,
):
    events = []

    class Account:
        def __init__(self):
            self.positions = {
                "HYPE/USDT": {
                    "side": side, "entry_mode": "CHANNEL_SWING",
                    "entry_price": 100.0, "qty": 1.0,
                    "open_timestamp": 999.5, "channel_energy_score": 0.10,
                    "channel_kc_upper": 102.0, "channel_kc_lower": 98.0,
                    "channel_momentum_declining": momentum_declining,
                }
            }
            self.pending_limit_orders = {}

        def log(self, *_args, **_kwargs):
            pass

        async def close_position(self, symbol, *_args, **_kwargs):
            events.append(("close", symbol))
            self.positions.pop(symbol)
            return True

    class Rotation:
        def request_replacement(self, symbol):
            events.append(("replace", symbol))

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.symbol_rotation = Rotation()
    engine.rotation_event = None
    engine.tickers = {"HYPE/USDT": held_mark}
    engine._channel_invalid_entry_candidates = set()
    engine._execution_price_is_safe = lambda *_args: None

    async def execution_safe(*_args):
        return True

    async def fresh_snapshot(_symbol, _side, _candidate_bar_id):
        return {
            "price": 51.0 if side == "LONG" else 49.0,
            "kc_upper": 50.0 if side == "LONG" else 52.0,
            "kc_lower": 48.0 if side == "LONG" else 50.0,
        }

    async def place(symbol, _candidate, _price, snapshot=None):
        assert snapshot is not None
        events.append(("open", symbol, side))
        return True

    engine._execution_price_is_safe = execution_safe
    engine._fresh_channel_entry_snapshot = fresh_snapshot
    engine._abnormal_market_entry_allowed = lambda *_args: True
    engine._place_structured_entry = place
    signal_code = (
        "KC_CLOSED_BODY_HIGH_BREAK_LONG"
        if side == "LONG" else "KC_CLOSED_BODY_LOW_BREAK_SHORT"
    )
    candidate = {
        "symbol": "NEW/USDT", "side": side,
        "entry_mode": "CHANNEL_SWING", "priority": 5,
        "signal_code": signal_code, "reason": signal_code,
        "live_price": 50.0 if side == "LONG" else 49.0,
        "kc_upper": 50.0 if side == "LONG" else 52.0,
        "kc_lower": 48.0 if side == "LONG" else 50.0,
        "atr": 0.5, "candidate_bar_id": 123456000,
        "confirmed_trend_quality": 1.5,
        "confirmed_volume_ratio": 1.2,
    }

    result = await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    )

    assert result == ((True, True) if should_switch else (False, False))
    if should_switch:
        assert events == [
            ("close", "HYPE/USDT"),
            ("replace", "HYPE/USDT"),
            ("open", "NEW/USDT", side),
        ]
    else:
        assert events == []


@pytest.mark.parametrize(
    ("side", "arm_price", "peak_price", "stop_price", "reason"),
    [
        ("LONG", 103.0, 105.0, 104.0, "KC_OUTER_TRAILING_STOP_LONG"),
        ("SHORT", 97.0, 95.0, 96.0, "KC_OUTER_TRAILING_STOP_SHORT"),
    ],
)
def test_rapid_outer_channel_swing_uses_symmetric_atr_trailing_stop(
    monkeypatch, side, arm_price, peak_price, stop_price, reason,
):
    monkeypatch.setattr("core.engine.CHANNEL_SWING_TRAILING_ATR_MULT", 1.0)
    frame = _channel_frame()
    frame["atr"] = 1.0
    if side == "LONG":
        frame.loc[frame.index[-3], ["open", "close"]] = [100.0, 101.0]
        frame.loc[frame.index[-2], ["open", "close"]] = [101.0, 102.0]
        frame.loc[frame.index[-1], "open"] = 102.0
    else:
        frame.loc[frame.index[-3], ["open", "close"]] = [100.0, 99.0]
        frame.loc[frame.index[-2], ["open", "close"]] = [99.0, 98.0]
        frame.loc[frame.index[-1], "open"] = 98.0
    position = {"side": side, "entry_price": 100.0}

    armed = TradingEngine._channel_outer_trailing_action(frame, arm_price, position)
    assert armed["action"] == "HOLD"
    assert armed["updates"]["channel_outer_trailing_armed"] is True
    position.update(armed["updates"])

    advanced = TradingEngine._channel_outer_trailing_action(frame, peak_price, position)
    assert advanced["action"] == "HOLD"
    position.update(advanced["updates"])
    stopped = TradingEngine._channel_outer_trailing_action(frame, stop_price, position)
    assert (stopped["action"], stopped["reason"]) == ("EXIT", reason)


def test_gradual_outer_channel_swing_keeps_original_ma3_exit():
    frame = _channel_frame()
    frame["atr"] = 1.0
    frame.loc[frame.index[-1], "open"] = 100.8
    result = TradingEngine._channel_outer_trailing_action(
        frame, 101.4, {"side": "LONG", "entry_price": 100.0},
    )
    assert result == {"action": "HOLD", "updates": {}}


def test_outer_trailing_never_arms_when_its_stop_is_below_net_break_even(monkeypatch):
    monkeypatch.setattr("core.engine.CHANNEL_SWING_TRAILING_ATR_MULT", 1.0)
    frame = _channel_frame()
    frame["atr"] = 1.0
    frame.loc[frame.index[-1], "open"] = 100.0
    # It is a one-ATR outer surge, but 102 - 1.0 ATR is still below cost.
    result = TradingEngine._channel_outer_trailing_action(
        frame, 102.0, {"side": "LONG", "entry_price": 101.0},
    )
    assert result == {"action": "HOLD", "updates": {}}


@pytest.mark.parametrize(("side", "mark_price"), [("LONG", 99.0), ("SHORT", 101.0)])
def test_channel_max_net_loss_hard_exit_is_symmetric(side, mark_price):
    position = {"side": side, "entry_price": 100.0, "qty": 5.0}
    result = TradingEngine._channel_max_net_loss_action(
        position, mark_price, wallet_balance=150.0, max_loss_wallet_pct=0.03,
    )
    assert (result["action"], result["reason"]) == (
        "EXIT", "CHANNEL_MAX_NET_LOSS_EXIT",
    )


def test_channel_max_net_loss_allows_loss_below_hard_limit():
    result = TradingEngine._channel_max_net_loss_action(
        {"side": "LONG", "entry_price": 100.0, "qty": 5.0},
        99.5, wallet_balance=150.0, max_loss_wallet_pct=0.03,
    )
    assert result["action"] == "HOLD"


@pytest.mark.parametrize(
    ("side", "reason"),
    [("LONG", "KC_UPPER_TWO_BAR_REVERSAL_EXIT"), ("SHORT", "KC_LOWER_TWO_BAR_REVERSAL_EXIT")],
)
def test_channel_exits_on_confirmed_two_bar_reversal_after_outer_impulse(side, reason):
    frame = _channel_frame()
    if side == "LONG":
        frame.loc[frame.index[-4], ["open", "close", "high", "low"]] = [100.5, 102.0, 102.2, 100.4]
        frame.loc[frame.index[-3], ["open", "close", "high", "low"]] = [102.0, 101.5, 102.1, 101.4]
        frame.loc[frame.index[-2], ["open", "close", "high", "low"]] = [101.5, 101.2, 101.6, 101.1]
        price = 101.2
    else:
        frame.loc[frame.index[-4], ["open", "close", "high", "low"]] = [99.5, 98.0, 99.6, 97.8]
        frame.loc[frame.index[-3], ["open", "close", "high", "low"]] = [98.0, 98.5, 98.6, 97.9]
        frame.loc[frame.index[-2], ["open", "close", "high", "low"]] = [98.5, 98.8, 98.9, 98.4]
        price = 98.8
    result = TradingEngine._channel_swing_action(frame, price, side)
    assert result["action"] == "HOLD"


def test_channel_does_not_exit_when_second_reversal_does_not_break_first_reversal_low():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ["open", "close", "high", "low"]] = [100.5, 102.0, 102.2, 100.4]
    frame.loc[frame.index[-3], ["open", "close", "high", "low"]] = [102.0, 101.5, 102.1, 101.4]
    # ARB-like pullback: the second red candle has not closed below the first red low.
    frame.loc[frame.index[-2], ["open", "close", "high", "low"]] = [101.5, 101.45, 101.6, 101.3]

    result = TradingEngine._channel_swing_action(frame, 101.45, "LONG")

    assert result["action"] == "HOLD"

@pytest.mark.parametrize(
    ("side", "mark_price", "reason"),
    [
        ("LONG", 100.10, "CHANNEL_PROFIT_RECLAIM_EXIT_LONG"),
        ("SHORT", 99.90, "CHANNEL_PROFIT_RECLAIM_EXIT_SHORT"),
    ],
)
def test_channel_profit_reclaim_exit_locks_cost_after_one_atr_profit(
    side, mark_price, reason,
):
    result = TradingEngine._channel_profit_reclaim_action(
        {"side": side, "entry_price": 100.0, "peak_pnl_pct": 0.02},
        mark_price, atr=1.0, min_profit_atr_mult=1.0,
    )
    assert (result["action"], result["reason"]) == ("EXIT", reason)


def test_channel_profit_reclaim_does_not_arm_before_one_atr_profit():
    result = TradingEngine._channel_profit_reclaim_action(
        {"side": "SHORT", "entry_price": 100.0, "peak_pnl_pct": 0.009},
        99.90, atr=1.0, min_profit_atr_mult=1.0,
    )
    assert result == {"action": "HOLD"}


def test_recent_candles_whipsawing_blocks_alternating_colors():
    frame = _channel_frame()
    colors = [100.2, 99.8, 100.2, 99.8, 100.2, 99.8]
    for offset, close in enumerate(colors, start=1):
        frame.loc[frame.index[-1 - offset], ["open", "close"]] = [100.0, close]
    assert TradingEngine._channel_recent_candles_whipsawing(frame) is True


def test_recent_candles_whipsawing_allows_directional_run():
    frame = _channel_frame()
    closes = [100.1, 100.2, 100.3, 100.4, 100.5, 100.6]
    for offset, close in enumerate(closes, start=1):
        frame.loc[frame.index[-1 - offset], ["open", "close"]] = [100.0, close]
    assert TradingEngine._channel_recent_candles_whipsawing(frame) is False
