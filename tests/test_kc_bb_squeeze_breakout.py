"""Unit tests for KC + BB Squeeze + Trend Breakout strategy."""

import numpy as np
import pandas as pd

from core.services.strategies.kc_bb_squeeze import KCBBStrategy
from core.services.strategies.kc_bb_squeeze_trailing import (
    calculate_squeeze_kc_breakout_strategy,
)


def _make_base_df(n: int = 40, base_price: float = 100.0) -> pd.DataFrame:
    closes = np.full(n, base_price, dtype=float)
    highs = closes + 0.2
    lows = closes - 0.2
    opens = closes.copy()
    volumes = np.full(n, 1000.0)
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def test_squeeze_blocks_entry():
    """When BB is strictly inside KC (is_sqz == True), no entry should be triggered."""
    df = _make_base_df(30, 100.0)
    # Tightly compressed bars with wide high-low range so ATR/KC is wide while BB std is tiny
    df["high"] = 105.0
    df["low"] = 95.0
    df["close"] = 100.0 + np.linspace(0, 0.2, 30)

    res = calculate_squeeze_kc_breakout_strategy(df, min_kc_slope=0.00001)
    assert bool(res["is_sqz"].iloc[-1]) is True
    assert bool(res["can_trade"].iloc[-1]) is False
    assert (res["signal"] == 0).all()


def test_long_trend_breakout_and_trailing_or_midline_exit():
    """Verify Long breakout (signal=1) when close > kc_upper and slope > min_kc_slope, then exit (signal=2)."""
    # Oscillate around 100 so BB is wider than KC (is_sqz == False), then break out upward
    prices = [100.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(20)]
    # Stay inside kc_upper while nudging midline slope upward
    prices.extend([100.5, 101.0, 101.2])
    # Sharp breakout bar crossing above kc_upper
    prices.append(108.0)
    # Further rally to 112.0
    prices.append(112.0)
    # Sharp drop > 1.5% (112 * 0.985 = 110.32) to trigger trailing stop exit
    prices.append(109.0)

    closes = np.array(prices, dtype=float)
    df = pd.DataFrame(
        {
            "open": closes - 0.1,
            "high": closes + 0.2,
            "low": closes - 0.2,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
        }
    )

    res = calculate_squeeze_kc_breakout_strategy(
        df, min_kc_slope=0.0003, trailing_stop_pct=0.015
    )
    signals = res["signal"].tolist()
    assert 1 in signals, "Expected long breakout signal (1)"
    long_idx = signals.index(1)
    assert 2 in signals[long_idx + 1 :], "Expected long exit signal (2) after entry"


def test_short_trend_breakout_and_exit():
    """Verify Short breakout (signal=-1) when close < kc_lower and slope < -min_kc_slope, then exit (signal=-2)."""
    prices = [100.0 + (-2.0 if i % 2 == 0 else 2.0) for i in range(20)]
    prices.extend([99.5, 99.0, 98.8])
    prices.append(92.0)
    prices.append(88.0)
    # Rebound > 1.5% (88 * 1.015 = 89.32) to trigger trailing stop exit
    prices.append(91.0)

    closes = np.array(prices, dtype=float)
    df = pd.DataFrame(
        {
            "open": closes + 0.1,
            "high": closes + 0.2,
            "low": closes - 0.2,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
        }
    )

    res = calculate_squeeze_kc_breakout_strategy(
        df, min_kc_slope=0.0003, trailing_stop_pct=0.015
    )
    signals = res["signal"].tolist()
    assert -1 in signals, "Expected short breakout signal (-1)"
    short_idx = signals.index(-1)
    assert -2 in signals[short_idx + 1 :], "Expected short exit signal (-2) after entry"


def test_kcbb_strategy_class_compatibility():
    """Ensure KCBBStrategy class produces aligned indicators and signals."""
    prices = [100.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(20)] + [100.5, 101.0, 101.2, 108.0, 112.0, 109.0]
    closes = np.array(prices, dtype=float)
    df = pd.DataFrame(
        {
            "open": closes - 0.1,
            "high": closes + 0.2,
            "low": closes - 0.2,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
        }
    )
    strategy = KCBBStrategy(squeeze_min_slope=0.0003, trailing_stop_pct=0.015)
    out = strategy.generate_signals(df)
    assert "LONG" in out["signal"].values
    assert 1 in out["position_signal"].values
    assert 2 in out["position_signal"].values


def test_trend_direction_guard_blocks_counter_trend_entries():
    """
    Verify Trend Direction Guard:
    - When kc_slope > 0, NEVER trigger Short (-1), even if fast MA crosses below slow MA or price dips.
      A fast MA death cross during uptrend can only close an existing Long (2), never open Short (-1).
    - When kc_slope < 0, NEVER trigger Long (1), even if fast MA crosses above slow MA.
    """
    # Strong uptrend where kc_slope remains > 0, followed by a brief pullback causing fast MA death cross
    prices = [100.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(20)]
    prices.extend([101.0, 103.0, 106.0, 110.0, 115.0, 120.0, 117.0, 115.5, 115.0])
    closes = np.array(prices, dtype=float)
    df = pd.DataFrame(
        {
            "open": closes - 0.1,
            "high": closes + 0.2,
            "low": closes - 0.2,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
        }
    )
    res = calculate_squeeze_kc_breakout_strategy(df, min_kc_slope=0.0003)

    # Wherever kc_slope > 0, allow_short MUST be False and signal MUST NOT be -1
    up_slope_mask = res["kc_slope"] > 0
    assert not res.loc[up_slope_mask, "allow_short"].any()
    assert (-1 not in res.loc[up_slope_mask, "signal"].values)

    # Strong downtrend where kc_slope < 0: allow_long MUST be False and signal MUST NOT be 1
    down_prices = [100.0 + (-2.0 if i % 2 == 0 else 2.0) for i in range(20)]
    down_prices.extend([99.0, 97.0, 94.0, 90.0, 85.0, 80.0, 83.0, 84.5, 85.0])
    down_closes = np.array(down_prices, dtype=float)
    down_df = pd.DataFrame(
        {
            "open": down_closes + 0.1,
            "high": down_closes + 0.2,
            "low": down_closes - 0.2,
            "close": down_closes,
            "volume": np.full(len(down_closes), 1000.0),
        }
    )
    down_res = calculate_squeeze_kc_breakout_strategy(down_df, min_kc_slope=0.0003)
    down_slope_mask = down_res["kc_slope"] < 0
    assert not down_res.loc[down_slope_mask, "allow_long"].any()
    assert (1 not in down_res.loc[down_slope_mask, "signal"].values)


def test_ma_distance_filter_blocks_sticky_midline_entries():
    """
    Verify MA Distance Filter (ma_dist = abs(ma3 - kc_middle) / kc_middle):
    When ma_dist <= min_ma_dist, can_open / allow_long / allow_short must be False
    and no entry signal (1 or -1) is allowed even if slope and breakout conditions are met.
    """
    prices = [100.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(20)] + [
        100.5,
        101.0,
        101.2,
        108.0,
        112.0,
    ]
    closes = np.array(prices, dtype=float)
    df = pd.DataFrame(
        {
            "open": closes - 0.1,
            "high": closes + 0.2,
            "low": closes - 0.2,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
        }
    )

    # With a very large min_ma_dist (e.g., 0.50 = 50%), all bars are considered "sticky" to midline
    res_blocked = calculate_squeeze_kc_breakout_strategy(
        df, min_kc_slope=0.0003, min_ma_dist=0.50
    )
    assert "ma_dist" in res_blocked.columns
    assert not res_blocked["can_open"].any()
    assert (res_blocked["signal"] == 0).all()

    # Verify sticky bars in normal threshold (min_ma_dist=0.001) never trigger entry
    res_normal = calculate_squeeze_kc_breakout_strategy(
        df, min_kc_slope=0.0003, min_ma_dist=0.001
    )
    sticky_mask = res_normal["ma_dist"] <= 0.001
    assert not res_normal.loc[sticky_mask, "can_open"].any()
    assert (res_normal.loc[sticky_mask, "signal"] == 0).all()


def test_pivot_peak_trough_confirmation_filters_micro_crosses():
    """
    Verify Pivot Peak/Trough Confirmation:
    - Long cross requires close > trough * (1 + min_pivot_rebound_pct) and kc_slope > 0.0003.
    - Short cross requires close < peak * (1 - min_pivot_rebound_pct) and kc_slope < -0.0003.
    - Micro crosses that fail to move 0.25% away from the 10-bar trough/peak must be blocked.
    """
    # Build scenario where a cross happens, and test blocking when min_pivot_rebound_pct is not met
    prices = [100.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(20)] + [
        100.5,
        101.0,
        101.2,
        108.0,
        112.0,
    ]
    closes = np.array(prices, dtype=float)
    df = pd.DataFrame(
        {
            "open": closes - 0.1,
            "high": closes + 0.2,
            "low": closes - 0.2,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
        }
    )

    # Normal 0.25% threshold: 108.0 is well above 10-bar trough (~97.8), so entry is allowed
    res_valid = calculate_squeeze_kc_breakout_strategy(
        df, min_kc_slope=0.0003, min_pivot_rebound_pct=0.0025
    )
    assert bool(res_valid["valid_trough_rebound"].iloc[23]) is True
    assert 1 in res_valid["signal"].values

    # Unrealistically high rebound requirement (e.g., 50% above 10-bar trough): blocks entry
    res_blocked = calculate_squeeze_kc_breakout_strategy(
        df, min_kc_slope=0.0003, min_pivot_rebound_pct=0.50
    )
    assert not res_blocked["valid_trough_rebound"].any()
    assert not res_blocked["valid_trough_cross"].any()
    assert (res_blocked["signal"] == 0).all()


def test_long_exit_holds_normal_pullback_and_triggers_on_overbought_or_waterfall():
    """
    Verify Long exit refinement:
    1. Normal pullback inside channel (close >= kc_middle, drop <= 1.2%) holds position without exiting.
    2. Overbought Adaptive Trailing Stop: once highest_price > kc_upper, a >0.8% pullback triggers OVERBOUGHT_TRAILING_STOP.
    3. Waterfall crash (>1.2% single-bar drop or close < kc_lower) immediately triggers WATERFALL_EXIT.
    """
    # Scenario A: Overbought trailing stop (0.8% pullback from peak > kc_upper)
    prices_ob = [100.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(20)] + [
        100.5,
        101.0,
        101.2,
        108.0,  # Breakout entry above kc_upper (~105)
        110.0,  # Peak in overbought zone
        109.5,  # Mild 0.45% pullback (< 0.8% threshold, above kc_middle) -> MUST HOLD (signal == 0)
        109.0,  # 0.91% pullback from 110.0 (> 0.8% threshold, single bar drop 0.45% < 1.2%) -> OVERBOUGHT_TRAILING_STOP
    ]
    closes_ob = np.array(prices_ob, dtype=float)
    df_ob = pd.DataFrame(
        {
            "open": closes_ob,
            "high": closes_ob + 0.1,
            "low": closes_ob - 0.1,
            "close": closes_ob,
            "volume": np.full(len(closes_ob), 1000.0),
        }
    )
    res_ob = calculate_squeeze_kc_breakout_strategy(df_ob, min_kc_slope=0.0003)
    # Bar 23 is entry (108.0), Bar 24 is 110.0 (hold), Bar 25 is 109.5 (0.45% pullback -> hold!), Bar 26 is 109.0 (exit!)
    assert res_ob["signal"].iloc[23] == 1
    assert res_ob["signal"].iloc[25] == 0, "Should hold through mild 0.45% pullback"
    assert res_ob["signal"].iloc[26] == 2
    assert res_ob["exit_reason"].iloc[26] == "OVERBOUGHT_TRAILING_STOP"

    # Scenario B: Waterfall crash (> 1.2% single-bar drop)
    prices_wf = [100.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(20)] + [
        100.5,
        101.0,
        101.2,
        108.0,  # Entry
        106.5,  # Single bar drop from 108.0 to 106.5 is 1.39% (> 1.2% waterfall threshold)
    ]
    closes_wf = np.array(prices_wf, dtype=float)
    df_wf = pd.DataFrame(
        {
            "open": closes_wf - 0.1,
            "high": closes_wf + 0.1,
            "low": closes_wf - 0.1,
            "close": closes_wf,
            "volume": np.full(len(closes_wf), 1000.0),
        }
    )
    res_wf = calculate_squeeze_kc_breakout_strategy(df_wf, min_kc_slope=0.0003)
    assert res_wf["signal"].iloc[23] == 1
    assert res_wf["signal"].iloc[24] == 2
    assert res_wf["exit_reason"].iloc[24] == "WATERFALL_EXIT"




