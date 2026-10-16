import pandas as pd
import pytest

from core.gates.macro_candle_structure_gate import MacroCandleStructureGate
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


def _pivot_cross_frame(side):
    frame = _frame(side)
    for index in range(len(frame)):
        frame.loc[index, [
            "open", "high", "low", "close", "volume", "kc_upper",
            "kc_middle", "kc_lower", "ma5", "ma15",
        ]] = [100.0, 100.3, 99.7, 100.05, 10.0, 101.0, 100.0, 99.0, 99.8, 100.0]
    if side == "LONG":
        frame.loc[8, ["high", "low"]] = [100.2, 99.2]
        frame.loc[9, ["open", "high", "low", "close", "kc_lower"]] = [
            99.0, 99.6, 98.0, 99.2, 98.1,
        ]
        frame.loc[10, ["high", "low", "ma5", "ma15"]] = [100.1, 99.0, 99.9, 100.0]
        frame.loc[11, [
            "open", "high", "low", "close", "ma5", "ma15",
        ]] = [99.9, 100.5, 99.8, 100.4, 100.1, 100.0]
    else:
        frame.loc[8, ["high", "low"]] = [100.8, 99.8]
        frame.loc[9, ["open", "high", "low", "close", "kc_upper"]] = [
            101.0, 102.0, 100.4, 100.8, 101.9,
        ]
        frame.loc[10, ["high", "low", "ma5", "ma15"]] = [101.2, 99.9, 100.1, 100.0]
        frame.loc[11, [
            "open", "high", "low", "close", "ma5", "ma15",
        ]] = [100.0, 100.1, 99.5, 99.6, 99.9, 100.0]
    return frame


def _profit_lock_continuation_frame(side):
    frame = _pivot_cross_frame(side)
    if side == "LONG":
        frame.loc[10, ["kc_upper", "kc_middle", "ma5", "ma15", "close", "high"]] = [
            100.2, 100.0, 100.3, 100.3, 100.4, 100.5,
        ]
        frame.loc[11, [
            "kc_upper", "kc_middle", "ma5", "ma15", "kc_lower",
            "open", "high", "low", "close",
        ]] = [100.2, 100.1, 100.4, 100.4, 98.0, 100.4, 100.7, 100.3, 100.6]
    else:
        frame.loc[10, ["kc_lower", "kc_middle", "ma5", "ma15", "close", "low"]] = [
            99.8, 100.0, 99.7, 99.7, 99.6, 99.5,
        ]
        frame.loc[11, [
            "kc_lower", "kc_middle", "ma5", "ma15", "kc_upper",
            "open", "high", "low", "close",
        ]] = [99.8, 99.9, 99.6, 99.6, 102.0, 99.6, 99.7, 99.3, 99.4]
    return frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_initial_entry_allows_ma15_flat_or_turning_against_ma5_cross(side):
    frame = _pivot_cross_frame(side)
    frame["atr"] = 2.0
    if side == "LONG":
        frame.loc[10, "ma15"] = 100.2
        frame.loc[11, "ma15"] = 100.0
    else:
        frame.loc[10, "ma15"] = 99.8
        frame.loc[11, "ma15"] = 100.0

    signal = WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, side)
    assert signal is not None
    assert signal["side"] == side
    assert signal["reason"] == "REVERSAL_CROSSOVER_ENTRY"
    assert signal["entry_pattern"] == "CROSSOVER"
    assert signal["structure_confirmation"] == (
        "CONFIRMED_KC_OUTER_PIVOT_THEN_MA5_MA15_CROSS"
    )
    assert signal["bar_id"] == frame.iloc[-1]["timestamp"]
    assert (
        frame.iloc[-1]["ma15"] - frame.iloc[-2]["ma15"] < 0
        if side == "LONG"
        else frame.iloc[-1]["ma15"] - frame.iloc[-2]["ma15"] > 0
    )

    current = frame.iloc[-1]
    allowed, reason = MacroCandleStructureGate().can_open(
        side,
        {
            name: float(current[name])
            for name in ("open", "high", "low", "close")
        },
        {
            name: float(current[name])
            for name in ("kc_upper", "kc_lower", "kc_middle", "ma15", "atr")
        } | {
            "ma5_current": signal["ma5_current"],
            "ma5_previous": signal["ma5_previous"],
        },
        entry_pattern=signal["entry_pattern"],
        structure_confirmation=signal["structure_confirmation"],
        structure_confirmed=signal["structure_confirmed"],
        channel_expanding=signal["channel_expanding"],
    )
    assert allowed is True
    assert reason == f"PASSED_MACRO_{side}_GATE"

    live_frame = frame.copy()
    live_frame.loc[live_frame.index[-1], "is_closed"] = False
    assert WildSwingStrategy().evaluate_entry("LOBSTER/USDT", live_frame, side) is None


@pytest.mark.parametrize(
    "side,updates",
    [
        (
            "SHORT",
            {
                -2: {
                    "open": 100.2, "high": 100.3, "low": 99.9, "close": 100.0,
                    "kc_upper": 101.5, "kc_lower": 99.8,
                },
                -1: {
                    "open": 99.9, "high": 100.0, "low": 98.8, "close": 99.0,
                    "kc_upper": 101.2, "kc_lower": 99.2,
                },
            },
        ),
        (
            "LONG",
            {
                -2: {
                    "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0,
                    "kc_upper": 100.2, "kc_lower": 98.5,
                },
                -1: {
                    "open": 100.1, "high": 101.2, "low": 100.0, "close": 101.0,
                    "kc_upper": 100.8, "kc_lower": 98.3,
                },
            },
        ),
    ],
)
def test_expanding_outer_band_breakout_with_solid_body_creates_breakout_signal(
    side, updates,
):
    frame = _frame(side)
    frame["atr"] = 2.0
    for offset, values in updates.items():
        row_index = frame.index[offset]
        for name, value in values.items():
            frame.loc[row_index, name] = value

    signal = WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, side)

    assert signal is not None
    assert signal["side"] == side
    assert signal["reason"] == "KC_OUTER_BREAKOUT_ENTRY"
    assert signal["entry_pattern"] == "BREAKOUT"
    assert signal["structure_confirmation"] == "KC_OUTER_BREAKOUT"
    assert signal["body_ratio"] > 0.60


def test_breakout_entry_rejects_body_ratio_at_or_below_sixty_percent():
    frame = _frame("SHORT")
    previous_index, current_index = frame.index[-2], frame.index[-1]
    frame.loc[previous_index, ["close", "kc_lower"]] = [100.0, 99.8]
    frame.loc[current_index, [
        "open", "high", "low", "close", "kc_upper", "kc_lower",
    ]] = [99.9, 100.5, 98.5, 99.0, 101.2, 99.2]

    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, "SHORT",
    ) is None


def test_consecutive_green_candles_without_new_outer_pivot_and_cross_do_not_enter():
    frame = _frame("LONG")
    frame.loc[8:, "open"] = 99.8
    frame.loc[8:, "close"] = [99.9, 100.0, 100.1, 100.2]
    frame.loc[8:, "high"] = [100.0, 100.1, 100.2, 100.3]
    frame.loc[8:, "low"] = [99.7, 99.8, 99.9, 100.0]
    assert WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, "LONG") is None


def test_outer_band_breakout_is_not_an_initial_entry_by_itself():
    frame = _frame("LONG")
    frame.loc[10, ["open", "high", "low", "close"]] = [100.0, 101.2, 99.9, 101.1]
    frame.loc[11, ["open", "high", "low", "close"]] = [101.1, 101.8, 101.0, 101.6]
    assert WildSwingStrategy().evaluate_entry("LOBSTER/USDT", frame, "LONG") is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_profit_lock_exit_allows_only_aligned_outer_band_continuation(side):
    frame = _profit_lock_continuation_frame(side)
    signal = WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT",
        frame,
        side,
        profit_lock_exit={
            "side": side, "close_id": 600_001, "close_bar_id": 600_000,
        },
    )
    assert signal is not None
    assert signal["reason"] == "PROFIT_LOCK_CONTINUATION_ENTRY"
    assert signal["entry_pattern"] == "CONTINUATION"


def test_profit_lock_continuation_rejects_low_volume_stall():
    frame = _pivot_cross_frame("LONG")
    frame.loc[9:, ["high", "close"]] = [[100.2, 100.2]] * 3
    frame.loc[9:, "volume"] = [10.0, 10.0, 5.0]
    frame.loc[10, ["kc_upper", "kc_middle", "ma5", "ma15"]] = [
        100.2, 100.0, 100.3, 100.3,
    ]
    frame.loc[11, ["kc_upper", "kc_middle", "ma5", "ma15",
                   "kc_lower", "open", "low"]] = [
        100.2, 100.1, 100.4, 100.4, 98.0, 100.4, 100.3,
    ]
    assert WildSwingStrategy().evaluate_entry(
        "LOBSTER/USDT", frame, "LONG",
        profit_lock_exit={
            "side": "LONG", "close_id": 600_001, "close_bar_id": 600_000,
        },
    ) is None


def test_latest_profit_lock_exit_requires_most_recent_matching_close():
    trades = [
        {
            "symbol": "LOBSTER/USDT", "action": "CLOSE_LONG",
            "entry_mode": "WILD_SWING", "reason": "Wild Swing EXIT_BY_PROFIT_LOCK",
            "id": 1_740_000_000_000,
        },
        {
            "symbol": "LOBSTER/USDT", "action": "OPEN_LONG",
            "entry_mode": "WILD_SWING",
        },
    ]
    assert WildSwingStrategy.latest_profit_lock_exit(
        trades, "LOBSTER/USDT",
    ) is not None
    assert WildSwingStrategy.latest_profit_lock_exit(
        [trades[1], trades[0]], "LOBSTER/USDT",
    ) is None


def _pivot_exit_frame(side):
    if side == "LONG":
        highs = [101, 102, 103, 105, 102, 104, 106, 103, 101]
        lows = [100, 101, 102, 100, 99, 98, 101, 99, 97]
        closes = [100.5, 101.5, 102.5, 104, 100, 103, 105, 100, 97.5]
        volumes = [10, 10, 10, 20, 10, 10, 10, 10, 10]
    else:
        highs = [103, 102, 101, 102, 102, 103, 102, 104, 105]
        lows = [102, 101, 100, 98, 99, 97, 96, 98, 103]
        closes = [102.5, 101.5, 100.5, 99, 100, 98, 97, 102, 104]
        volumes = [10, 10, 10, 20, 10, 10, 10, 10, 10]
    rows = []
    for index in range(len(highs)):
        rows.append({
            "timestamp": index * 60_000,
            "open": (highs[index] + lows[index]) / 2,
            "high": highs[index],
            "low": lows[index],
            "close": closes[index],
            "volume": volumes[index],
            "kc_upper": 110,
            "kc_middle": 100,
            "kc_lower": 90,
            "ma5": 100,
            "ma15": 100,
            "is_closed": True,
        })
    return pd.DataFrame(rows)


@pytest.mark.parametrize(
    "side,expected_reason", [
        ("LONG", "EXIT_BY_TRUE_PEAK_VOLUME_DIVERGENCE"),
        ("SHORT", "EXIT_BY_TRUE_VALLEY_VOLUME_DIVERGENCE"),
    ],
)
def test_exit_requires_confirmed_true_pivot_volume_divergence_and_structure_break(
    side, expected_reason,
):
    frame = _pivot_exit_frame(side)
    result = WildSwingStrategy().evaluate_exit(
        "LOBSTER/USDT",
        {"side": side, "open_timestamp": 60.0},
        frame,
    )
    assert result is not None
    assert result["reason"] == expected_reason


def test_trend_loss_or_midline_cross_alone_does_not_exit():
    frame = _frame("SHORT")
    frame.loc[11, ["open", "high", "low", "close", "kc_middle"]] = [
        99.4, 100.2, 99.3, 100.1, 100.0,
    ]
    assert WildSwingStrategy().evaluate_exit(
        "LOBSTER/USDT", {"side": "SHORT", "open_timestamp": 60.0}, frame,
    ) is None


def test_profit_lock_exit_is_available_without_true_pivot_and_live_bar_never_exits():
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
