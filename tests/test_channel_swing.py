import pandas as pd

from core.engine import TradingEngine


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


def _confirm_trough(frame: pd.DataFrame):
    frame.loc[frame.index[-3:], "ma3"] = [99.7, 99.5, 99.7]
    frame.loc[frame.index[-1], ["open", "close"]] = [99.6, 99.8]


def _confirm_peak(frame: pd.DataFrame):
    frame.loc[frame.index[-3:], "ma3"] = [100.3, 100.5, 100.3]
    frame.loc[frame.index[-1], ["open", "close"]] = [100.4, 100.2]


def test_channel_swing_enters_in_outer_zones_without_touching_rails():
    frame = _channel_frame()
    frame.loc[frame.index[-2], "low"] = 99.25
    _confirm_trough(frame)

    low = TradingEngine._channel_swing_action(frame, 99.8)

    frame = _channel_frame()
    frame.loc[frame.index[-2], "high"] = 100.75
    _confirm_peak(frame)
    high = TradingEngine._channel_swing_action(frame, 100.2)

    assert low == {
        "action": "ENTER", "side": "LONG",
        "kc_upper": 101.0, "kc_lower": 99.0, "reason": "",
    }
    assert high == {
        "action": "ENTER", "side": "SHORT",
        "kc_upper": 101.0, "kc_lower": 99.0, "reason": "",
    }


def test_channel_swing_holds_between_entry_and_opposite_edge():
    frame = _channel_frame()

    assert TradingEngine._channel_swing_action(frame, 100.0, "LONG")["action"] == "HOLD"
    assert TradingEngine._channel_swing_action(frame, 100.0, "SHORT")["action"] == "HOLD"


def test_channel_swing_reverses_only_at_opposite_edge():
    frame = _channel_frame()
    frame.loc[frame.index[-2], "high"] = 100.75
    _confirm_peak(frame)
    long_exit = TradingEngine._channel_swing_action(frame, 100.2, "LONG")

    frame = _channel_frame()
    frame.loc[frame.index[-2], "low"] = 99.25
    _confirm_trough(frame)
    short_exit = TradingEngine._channel_swing_action(frame, 99.8, "SHORT")

    assert (long_exit["action"], long_exit["side"]) == ("REVERSE", "SHORT")
    assert (short_exit["action"], short_exit["side"]) == ("REVERSE", "LONG")


def test_channel_swing_waits_for_confirmed_pivot_after_reaching_outer_zone():
    frame = _channel_frame()
    frame.loc[frame.index[-2], "low"] = 99.25

    no_trough = TradingEngine._channel_swing_action(frame, 99.5, "SHORT")

    assert no_trough["action"] == "HOLD"
    assert no_trough["reason"] == "WAIT_TROUGH"

    frame = _channel_frame()
    frame.loc[frame.index[-2], "high"] = 100.75
    no_peak = TradingEngine._channel_swing_action(frame, 100.5, "LONG")

    assert no_peak["action"] == "HOLD"
    assert no_peak["reason"] == "WAIT_PEAK"


def test_channel_swing_does_not_reuse_an_old_outer_zone_touch_in_the_middle():
    frame = _channel_frame()
    frame.loc[frame.index[-5], "high"] = 100.9
    _confirm_peak(frame)
    # 真正形成 MA3 峰頂的倒數第二根仍在中間區，較早觸區不能帶過來反手。
    frame.loc[frame.index[-2], "high"] = 100.5

    result = TradingEngine._channel_swing_action(frame, 100.2, "LONG")

    assert result["action"] == "HOLD"
    assert result["side"] is None


def test_channel_swing_positions_are_not_managed_by_early_exit_loops():
    assert TradingEngine._is_continuous_wave_position({
        "entry_mode": "CHANNEL_SWING",
    })
