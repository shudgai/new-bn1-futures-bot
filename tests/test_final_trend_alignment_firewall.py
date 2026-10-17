import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.entry_firewall import (
    final_trend_alignment_problem,
    market_structure_gate_problem,
    ma_parallel_chop_problem,
    ma5_ma15_entry_gate_problem,
    require_final_trend_alignment,
)


def trend_frame(direction, side):
    middles = (
        list(map(float, range(88, 103)))
        if direction == "UP"
        else list(map(float, range(114, 99, -1)))
    )
    if side == "LONG":
        ma5_values = [87.0 + index for index in range(12)] + [99.0, 101.0, 103.0]
        ma15_values = [88.0 + index for index in range(12)] + [100.0, 102.0, 102.0]
    else:
        ma5_values = [113.0 - index for index in range(12)] + [101.0, 100.0, 99.0]
        ma15_values = [114.0 - index for index in range(12)] + [103.0, 102.0, 101.0]
    frame = pd.DataFrame([
        {
            "timestamp": (index + 1) * 60_000,
            "open": 100.0,
            "high": 104.0,
            "low": 96.0,
            "close": middle + 0.5,
            "ma5": ma5_values[index],
            "ma15": ma15_values[index],
            "atr": 2.0,
            "kc_middle": middle,
            "kc_upper": middle + 10.0,
            "kc_lower": middle - 10.0,
            "is_closed": True,
        }
        for index, middle in enumerate(middles)
    ])
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    return frame


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
def test_blocks_short_when_completed_kc_is_up(symbol):
    frame = trend_frame("UP", "SHORT")

    with pytest.raises(ValueError, match="BLOCKED_SHORT_KC_NOT_DOWN"):
        require_final_trend_alignment(frame, "SHORT", symbol)


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
def test_blocks_short_when_ma5_is_above_ma15(symbol):
    frame = trend_frame("DOWN", "LONG")

    with pytest.raises(ValueError, match="VETO_SHORT_MA_CONDITION_NOT_MET"):
        require_final_trend_alignment(frame, "SHORT", symbol)


def test_blocks_short_when_ma5_is_not_falling():
    frame = trend_frame("DOWN", "SHORT")
    frame.loc[frame.index[-1], "ma5"] = 100.1

    assert final_trend_alignment_problem(frame, "SHORT") == (
        "VETO_SHORT_MA_CONDITION_NOT_MET"
    )


def test_blocks_short_on_strong_bullish_candle():
    frame = trend_frame("DOWN", "SHORT")
    frame.loc[frame.index[-1], "close"] = 101.7

    assert final_trend_alignment_problem(frame, "SHORT") == (
        "BLOCKED_SHORT_BULLISH_IMPULSE"
    )


@pytest.mark.parametrize(
    "previous_close,quote",
    [(98.0, 99.1), (100.0, 101.1)],
)
def test_blocks_short_when_price_crosses_up_ma5_or_ma15(previous_close, quote):
    frame = trend_frame("DOWN", "SHORT")
    frame.loc[frame.index[-2], "close"] = previous_close

    assert final_trend_alignment_problem(
        frame, "SHORT", quote=quote,
    ) == "BLOCKED_SHORT_PRICE_CROSSED_UP_MA"


def test_blocks_short_when_live_ma5_turns_up():
    frame = trend_frame("DOWN", "SHORT")
    live = frame.iloc[-1].copy()
    live["timestamp"] = 960_000
    live["is_closed"] = False
    live["ma5"] = 99.2
    live["open"] = 100.0
    live["close"] = 100.1
    frame.loc[len(frame)] = live

    assert final_trend_alignment_problem(
        frame, "SHORT", quote=100.1,
    ) == "BLOCKED_SHORT_LIVE_MA5_TURNING_UP"


@pytest.mark.parametrize(
    "direction,side",
    [("UP", "LONG"), ("DOWN", "SHORT")],
)
def test_allows_only_aligned_kc_and_ma_direction(direction, side):
    assert final_trend_alignment_problem(trend_frame(direction, side), side) is None


@pytest.mark.parametrize(
    "direction,side,expected",
    [
        ("DOWN", "LONG", "BLOCKED_LONG_KC_NOT_UP"),
        ("UP", "SHORT", "BLOCKED_SHORT_KC_NOT_DOWN"),
    ],
)
def test_blocks_side_opposed_to_kc_trend(direction, side, expected):
    assert final_trend_alignment_problem(trend_frame(direction, side), side) == expected


def test_long_requires_a_fresh_golden_cross_and_rising_ma5():
    frame = trend_frame("UP", "LONG")
    assert final_trend_alignment_problem(frame, "LONG") is None

    frame.loc[frame.index[-2], "ma5"] = 102.5
    assert final_trend_alignment_problem(frame, "LONG") == (
        "BLOCKED_LONG_MA5_GOLDEN_CROSS_NOT_FRESH"
    )

    frame = trend_frame("UP", "LONG")
    frame.loc[frame.index[-2], "ma5"] = 101.5
    frame.loc[frame.index[-2], "ma15"] = 102.0
    frame.loc[frame.index[-1], "ma5"] = 101.0
    frame.loc[frame.index[-1], "ma15"] = 100.5
    assert final_trend_alignment_problem(frame, "LONG") == (
        "VETO_LONG_MA_CONDITION_NOT_MET"
    )


def test_long_blocks_price_below_kc_midline_even_in_uptrend():
    frame = trend_frame("UP", "LONG")
    assert final_trend_alignment_problem(
        frame, "LONG", quote=101.9,
    ) == "BLOCKED_LONG_PRICE_BELOW_KC_MIDDLE"


def test_long_blocks_live_kc_down_label():
    frame = trend_frame("UP", "LONG")
    frame.loc[frame.index[-1], "channel_state"] = "KC向下"
    assert final_trend_alignment_problem(frame, "LONG") == (
        "BLOCKED_LONG_KC_NOT_UP"
    )


def test_blocks_missing_closed_candle_data():
    assert final_trend_alignment_problem(pd.DataFrame(), "SHORT") == (
        "BLOCKED_FINAL_TREND_DATA_INSUFFICIENT"
    )


def test_hard_ma_relation_gate_rejects_long_below_ma15_even_with_rising_ma5():
    frame = trend_frame("UP", "LONG")
    frame.loc[frame.index[-1], "ma5"] = 101.0
    frame.loc[frame.index[-1], "ma15"] = 101.5

    assert ma5_ma15_entry_gate_problem(frame, "LONG") == (
        "VETO_LONG_MA_CONDITION_NOT_MET"
    )


def test_hard_ma_relation_gate_rejects_short_above_ma15_even_with_falling_ma5():
    frame = trend_frame("DOWN", "SHORT")
    frame.loc[frame.index[-1], "ma5"] = 101.5
    frame.loc[frame.index[-1], "ma15"] = 101.0

    assert ma5_ma15_entry_gate_problem(frame, "SHORT") == (
        "VETO_SHORT_MA_CONDITION_NOT_MET"
    )


def test_market_structure_gate_fails_closed_with_fewer_than_15_closed_bars():
    frame = trend_frame("UP", "LONG").tail(14).copy()

    assert market_structure_gate_problem(frame, "LONG") == (
        "BLOCKED_INSUFFICIENT_DATA"
    )


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("ma5", 103.0, "VETO_LINE_PARALLEL_OR_FLAT_MA5"),
        ("ma15", 102.0, "VETO_LINE_PARALLEL_OR_FLAT_MA15"),
        ("kc_upper", 112.0, "VETO_LINE_PARALLEL_OR_FLAT_KC_UPPER"),
        ("kc_middle", 100.0, "VETO_LINE_PARALLEL_OR_FLAT_KC_MIDDLE"),
        ("kc_lower", 90.0, "VETO_LINE_PARALLEL_OR_FLAT_KC_LOWER"),
    ],
)
def test_market_structure_gate_rejects_any_flat_ma_or_kc_line(
    column, value, reason,
):
    frame = trend_frame("UP", "LONG")
    frame.loc[frame.index[-3:], column] = value

    assert market_structure_gate_problem(frame, "LONG") == reason


def test_market_structure_gate_rejects_ma_compression():
    frame = trend_frame("UP", "LONG")
    frame.loc[frame.index[-1], "ma5"] = 102.1

    assert market_structure_gate_problem(frame, "LONG") == (
        "VETO_MA_TOO_CLOSE (dist: 0.10000000 < min: 0.30000000)"
    )


def test_market_structure_gate_rejects_repeated_ma_crosses():
    frame = trend_frame("UP", "LONG")
    offsets = [-0.7, 0.7, -0.7, 0.7, -0.7, 0.7, -0.7, 0.7, -0.7, 0.8]
    frame.loc[frame.index[-10:], "ma5"] = (
        frame.loc[frame.index[-10:], "ma15"].to_numpy() + offsets
    )

    assert market_structure_gate_problem(frame, "LONG") == (
        "VETO_FREQUENT_MA_CROSSES (count: 9 in 10 bars)"
    )


def test_market_structure_gate_rejects_tight_spacing_immediately_after_cross():
    frame = trend_frame("UP", "LONG")
    frame.loc[frame.index[-1], "ma5"] = 102.4

    assert market_structure_gate_problem(frame, "LONG") == (
        "VETO_CHOP_AFTER_CROSS (bars_since_cross: 1)"
    )


def test_market_structure_gate_rejects_regular_ma5_oscillation():
    frame = trend_frame("UP", "LONG")
    offsets = [0.6, 5.0, 0.6, 5.0, 0.6, 5.0, 0.6, 5.0, 0.6, 5.0, 0.6, 5.0]
    frame.loc[frame.index[-12:], "ma5"] = (
        frame.loc[frame.index[-12:], "ma15"].to_numpy() + offsets
    )

    assert market_structure_gate_problem(frame, "LONG") == (
        "VETO_REGULAR_WAVE_OSCILLATION (turns: 10 in 12 bars)"
    )


def ma_parallel_frame(*, side="LONG", release=False, release_volume=130.0):
    rows = [
        {
            "timestamp": (index + 1) * 60_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "ma5": (
                99.20 if release and side == "SHORT" and index == 3
                else 100.80 if release and index == 3
                else 100.00 + 0.01 * index
            ),
            "ma15": (
                99.80 if release and side == "SHORT" and index == 3
                else 100.23 if release and index == 3
                else 100.02 + 0.005 * index
            ),
            "atr": 1.0,
            "volume": release_volume if release and index == 3 else 100.0,
            "is_closed": True,
        }
        for index in range(4 if release else 2)
    ]
    if release:
        rows[-1].update(
            open=100.0 if side == "LONG" else 101.0,
            high=101.0,
            low=99.0,
            close=101.0 if side == "LONG" else 100.0,
        )
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def test_parallel_ma_gap_vetoes_both_directions():
    frame = ma_parallel_frame()

    assert ma_parallel_chop_problem(frame, "LONG", quote=100.0) == (
        "BLOCKED_MA_PARALLEL_CHOP"
    )
    assert ma_parallel_chop_problem(frame, "SHORT", quote=100.0) == (
        "BLOCKED_MA_PARALLEL_CHOP"
    )


def test_flat_ma_slopes_veto_even_when_gap_is_wider_than_threshold():
    frame = ma_parallel_frame()
    frame.loc[0, ["ma5", "ma15"]] = [100.0, 100.5]
    frame.loc[1, ["ma5", "ma15"]] = [100.01, 100.51]

    assert ma_parallel_chop_problem(frame, "LONG", quote=100.0) == (
        "BLOCKED_MA_PARALLEL_CHOP"
    )


def test_parallel_gate_unlocks_only_on_expansion_volume_and_directional_body():
    frame = ma_parallel_frame(release=True)

    assert ma_parallel_chop_problem(frame, "LONG", quote=101.0) is None


def test_parallel_gate_unlocks_short_after_bearish_expansion():
    frame = ma_parallel_frame(side="SHORT", release=True)

    assert ma_parallel_chop_problem(frame, "SHORT", quote=100.0) is None


def test_parallel_gate_uses_live_quote_for_release_when_quote_is_omitted():
    frame = ma_parallel_frame(release=True)
    frame.loc[3, "is_closed"] = False

    assert ma_parallel_chop_problem(frame, "LONG") is None


@pytest.mark.parametrize(
    "release_volume",
    [None, 119.0],
)
def test_parallel_gate_stays_locked_without_expansion_volume(
    release_volume,
):
    frame = ma_parallel_frame(release=True, release_volume=release_volume)

    assert ma_parallel_chop_problem(frame, "LONG", quote=101.0) == (
        "BLOCKED_MA_PARALLEL_CHOP"
    )


def test_exchange_order_boundary_blocks_top_reversal_short_in_bull_trend():
    from core.testnet_account import BinanceTestnetAccount

    account = object.__new__(BinanceTestnetAccount)
    account.exchange = SimpleNamespace(create_order=AsyncMock())
    account.entry_frame_provider = AsyncMock(
        return_value=trend_frame("UP", "SHORT"),
    )
    context = {
        "entry_signal_code": "AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT",
        "entry_phase": "KC_TOP_WATERFALL_FLIP",
    }

    with pytest.raises(ValueError, match="BLOCKED_SHORT_KC_NOT_DOWN"):
        asyncio.run(account._send_order(
            "CAP/USDT",
            "market",
            "sell",
            1.0,
            entry_context=context,
        ))

    account.exchange.create_order.assert_not_awaited()
