import pandas as pd
import pytest

from core.services.strategies.wild_swing_strategy import WildSwingStrategy


def _frame(side):
    rows = []
    direction = -1 if side == "SHORT" else 1
    for index in range(12):
        middle = 100 + direction * index * 0.1
        ma15 = middle + (0.2 if side == "SHORT" else -0.2)
        close = middle + (0.4 if side == "SHORT" else -0.4)
        rows.append({
            "timestamp": index * 60_000,
            "open": close - direction * 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 10.0,
            "kc_upper": middle + 1.0,
            "kc_middle": middle,
            "kc_lower": middle - 1.0,
            "ma5": ma15 + (0.1 if side == "SHORT" else -0.1),
            "ma15": ma15,
            "is_closed": True,
        })

    if side == "SHORT":
        rows[-2].update(open=100.4, high=100.6, low=100.2, close=100.4,
                        kc_middle=100.0, ma15=100.2, ma5=100.3)
        rows[-1].update(open=100.1, high=100.2, low=99.4, close=99.5,
                        kc_middle=99.9, ma15=100.1, ma5=100.0)
    else:
        rows[-2].update(open=99.6, high=99.8, low=99.4, close=99.6,
                        kc_upper=101.0, kc_lower=99.0,
                        kc_middle=100.0, ma15=99.8, ma5=99.7)
        rows[-1].update(open=99.9, high=100.6, low=99.8, close=100.5,
                        kc_upper=102.0, kc_lower=99.0,
                        kc_middle=100.1, ma15=99.9, ma5=100.0)
    if side == "SHORT":
        rows[-2]["kc_upper"] = 101.5
        rows[-2]["kc_lower"] = 97.5
        rows[-1]["kc_upper"] = 101.5
        rows[-1]["kc_lower"] = 97.5
    return pd.DataFrame(rows)


def test_baseline_break_and_ma_cross_are_closed_bar_only_and_symmetric():
    for side in ("LONG", "SHORT"):
        strategy = WildSwingStrategy()
        frame = _frame(side)
        signal = strategy.evaluate_entry("LOBSTER/USDT", frame, side)
        assert signal["side"] == side
        assert signal["reason"] == "BASELINE_BREAK_AND_MA_CROSS"
        assert signal["entry_pattern"] == "CROSSOVER"
        assert signal["entry_signal_mode"] == "CROSSOVER"
        assert signal["bar_id"] == frame.iloc[-1]["timestamp"]

        strategy = WildSwingStrategy()
        live_frame = frame.copy()
        live_frame.loc[live_frame.index[-1], "is_closed"] = False
        assert strategy.evaluate_entry("LOBSTER/USDT", live_frame, side) is None


def test_reentry_accepts_one_or_two_counter_candles_without_volume_gate():
    frame = _frame("SHORT")
    frame.loc[10, ["open", "high", "low", "close"]] = [99.9, 100.1, 99.7, 100.0]
    frame.loc[10, ["kc_middle", "ma15", "ma5"]] = [100.0, 100.1, 100.2]
    frame.loc[11, ["open", "high", "low", "close"]] = [99.9, 100.0, 99.0, 99.1]
    frame.loc[11, ["kc_middle", "ma15", "ma5"]] = [99.9, 100.0, 99.8]
    frame.loc[11, "volume"] = 1.0

    signal = WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, "SHORT")
    assert signal["reason"] == "TREND_CONTINUATION"
    assert signal["entry_pattern"] == "CONTINUATION"
    assert signal["entry_signal_mode"] == "CONTINUATION"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_outer_band_breakout_has_explicit_mode_and_expanding_channel(side):
    frame = _frame(side)
    if side == "LONG":
        frame.loc[10, ["open", "high", "low", "close", "kc_upper", "kc_lower",
                       "kc_middle", "ma15", "ma5"]] = [
            100.0, 100.4, 99.9, 100.2, 101.0, 99.0, 100.0, 99.8, 100.0,
        ]
        frame.loc[11, ["open", "high", "low", "close", "kc_upper", "kc_lower",
                       "kc_middle", "ma15", "ma5"]] = [
            101.0, 101.7, 100.9, 101.5, 101.3, 98.7, 100.1, 99.9, 100.2,
        ]
    else:
        frame.loc[10, ["open", "high", "low", "close", "kc_upper", "kc_lower",
                       "kc_middle", "ma15", "ma5"]] = [
            100.0, 100.1, 99.6, 99.8, 101.0, 99.0, 100.0, 100.2, 100.0,
        ]
        frame.loc[11, ["open", "high", "low", "close", "kc_upper", "kc_lower",
                       "kc_middle", "ma15", "ma5"]] = [
            98.9, 99.0, 98.1, 98.3, 101.3, 98.7, 99.9, 100.1, 99.8,
        ]

    signal = WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, side)

    assert signal is not None
    assert signal["entry_pattern"] == "BREAKOUT"
    assert signal["entry_signal_mode"] == "BREAKOUT"
    assert signal["channel_expanding"] is True


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_kc_outer_pivot_reversal_has_explicit_mode(side):
    frame = _frame(side)
    if side == "LONG":
        frame.loc[9, ["open", "high", "low", "close"]] = [99.5, 100.0, 99.0, 99.6]
        frame.loc[10, ["open", "high", "low", "close", "kc_lower", "ma5", "ma15"]] = [
            98.8, 99.2, 98.5, 99.0, 98.6, 99.0, 99.1,
        ]
        frame.loc[11, ["open", "high", "low", "close", "kc_upper", "kc_lower",
                       "kc_middle", "ma5", "ma15"]] = [
            99.3, 100.4, 98.9, 100.2, 100.8, 98.4, 99.7, 100.0, 99.9,
        ]
    else:
        frame.loc[9, ["open", "high", "low", "close"]] = [100.2, 101.0, 99.9, 100.3]
        frame.loc[10, ["open", "high", "low", "close", "kc_upper", "ma5", "ma15"]] = [
            100.8, 101.5, 100.6, 101.0, 101.4, 100.9, 100.8,
        ]
        frame.loc[11, ["open", "high", "low", "close", "kc_upper", "kc_lower",
                       "kc_middle", "ma5", "ma15"]] = [
            101.1, 101.3, 100.4, 100.5, 102.0, 98.0, 100.8, 100.0, 100.1,
        ]

    signal = WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, side)

    assert signal is not None
    assert signal["entry_pattern"] == "REVERSAL"
    assert signal["entry_signal_mode"] == "REVERSAL"
    assert signal["structure_confirmed"] is True


def test_reset_clears_transient_blockers_and_anchors_at_latest_closed_bar():
    strategy = WildSwingStrategy()
    state = strategy.state_for("LOBSTER/USDT")
    state.bars_since_last_exit = 0
    state.last_exit_side = "SHORT"
    state.last_exit_price = 99.0
    state.last_exit_reason = "OLD_EXIT"
    state.last_exit_bar_id = 600_000
    state.veto_flags.add("OLD_VETO")
    frame = _frame("SHORT")
    original_rows = len(frame)

    strategy.reset_execution_state({"LOBSTER/USDT": frame.iloc[-1]["timestamp"]})

    reset_state = strategy.state_for("LOBSTER/USDT")
    assert reset_state.bars_since_last_exit == 999
    assert reset_state.last_exit_side is None
    assert reset_state.last_exit_price is None
    assert reset_state.last_exit_reason is None
    assert reset_state.last_exit_bar_id is None
    assert not reset_state.veto_flags
    assert reset_state.reset_bar_id == frame.iloc[-1]["timestamp"]
    assert len(frame) == original_rows
    assert strategy.evaluate_entry("LOBSTER/USDT", frame, "SHORT") is None

    next_bar = frame.iloc[-1].copy()
    next_bar["timestamp"] = frame.iloc[-1]["timestamp"] + 60_000
    next_bar["open"] = 99.3
    next_bar["high"] = 99.4
    next_bar["low"] = 98.9
    next_bar["close"] = 99.0
    next_bar["kc_middle"] = 99.8
    next_bar["ma15"] = 100.0
    next_bar["ma5"] = 99.8
    next_frame = pd.concat([frame, next_bar.to_frame().T], ignore_index=True)
    assert strategy.evaluate_entry("LOBSTER/USDT", next_frame, "SHORT") is not None


def test_short_long_lower_wick_waits_for_next_closed_bar_to_break_its_low():
    strategy = WildSwingStrategy()
    frame = _frame("SHORT")
    frame.loc[11, "low"] = 98.0
    assert strategy.evaluate_entry("LOBSTER/USDT", frame, "SHORT") is None
    assert strategy.state_for("LOBSTER/USDT").pending_short_wick_bar_id == frame.iloc[-1]["timestamp"]

    next_bar = frame.iloc[-1].copy()
    next_bar.update({
        "timestamp": float(frame.iloc[-1]["timestamp"]) + 60_000,
        "open": 98.6,
        "high": 98.7,
        "low": 97.8,
        "close": 97.9,
        "kc_middle": 99.8,
        "ma15": 100.0,
        "ma5": 99.7,
        "is_closed": False,
    })
    live_frame = pd.concat([frame, next_bar.to_frame().T], ignore_index=True)
    assert strategy.evaluate_entry("LOBSTER/USDT", live_frame, "SHORT") is None

    live_frame.loc[live_frame.index[-1], "is_closed"] = True
    signal = strategy.evaluate_entry("LOBSTER/USDT", live_frame, "SHORT")
    assert signal is not None
    assert signal["bar_id"] == next_bar["timestamp"]
    assert strategy.state_for("LOBSTER/USDT").pending_short_wick_bar_id is None


def test_short_wick_detector_rejects_ratio_body_and_doji_thresholds():
    detect = WildSwingStrategy._has_short_exhaustion_wick
    assert detect(pd.Series({"open": 10.0, "high": 11.0, "low": 9.3, "close": 10.5}))
    assert detect(pd.Series({"open": 10.0, "high": 10.2, "low": 8.7, "close": 9.8}))
    assert detect(pd.Series({"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.4}))
    assert not detect(pd.Series({"open": 10.0, "high": 10.6, "low": 9.8, "close": 10.5}))


def test_exit_uses_closed_midline_and_extreme_volume_only():
    strategy = WildSwingStrategy()
    frame = _frame("SHORT")
    position = {"side": "SHORT"}
    frame.loc[11, ["open", "high", "low", "close"]] = [99.4, 100.2, 99.3, 100.1]
    frame.loc[11, "kc_middle"] = 100.0
    result = strategy.evaluate_exit("LOBSTER/USDT", position, frame)
    assert result["reason"] == "EXIT_SHORT_ON_CONFIRMED_KC_MIDDLE_BREAK"

    strategy = WildSwingStrategy()
    frame = _frame("SHORT")
    frame.loc[11, ["open", "high", "low", "close"]] = [98.0, 98.1, 96.9, 97.8]
    frame.loc[11, ["kc_middle", "kc_lower", "volume"]] = [100.0, 98.0, 25.0]
    result = strategy.evaluate_exit("LOBSTER/USDT", position, frame)
    assert result["reason"] == "EXIT_SHORT_ON_EXTREME_VOLUME_PIN"


def test_profit_lock_precedes_strategy_exit_and_live_bar_never_exits():
    strategy = WildSwingStrategy()
    frame = _frame("LONG")
    frame.loc[11, "close"] = 99.5
    result = strategy.evaluate_exit(
        "LOBSTER/USDT", {"side": "LONG"}, frame, profit_lock_triggered=True,
    )
    assert result["reason"] == "EXIT_BY_PROFIT_LOCK"

    strategy = WildSwingStrategy()
    frame.loc[11, "is_closed"] = False
    strategy.reset_execution_state({"LOBSTER/USDT": frame.iloc[-2]["timestamp"]})
    assert strategy.evaluate_exit("LOBSTER/USDT", {"side": "LONG"}, frame) is None
