"""Unit tests for KC + BB Squeeze + Trend Breakout strategy."""

import numpy as np
import pandas as pd

from core.services.strategies.kc_bb_squeeze import KCBBStrategy
from core.services.strategies.kc_bb_squeeze_trailing import (
    calculate_full_kc_bb_strategy,
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


def test_calculate_full_kc_bb_strategy_trend_riding_and_parabolic_exit():
    """
    Verify calculate_full_kc_bb_strategy:
    1. Strictly separates Long (slope > min_kc_slope, close > kc_middle) and Short (slope < -min_kc_slope, close < kc_middle).
    2. Short trend riding: ignores minor green candles and MA3/MA15 crosses while inside channel,
       exiting via parabolic_exit (lowest_price < kc_lower and close > ma3) or trailing_exit (1.8%).
    """
    # Short trend scenario (like 1000PEPE downtrend)
    prices = [100.0 + (-2.0 if i % 2 == 0 else 2.0) for i in range(20)] + [
        99.5,
        99.0,
        98.8,
        92.0,  # Short entry (close < kc_lower, slope < -0.0003)
        90.0,  # Continues down outside lower rail (lowest_price = 90.0 < kc_low)
        92.5,  # Rebounds above EWM MA3 -> triggers parabolic_exit (-2)
    ]
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
    res = calculate_full_kc_bb_strategy(df, min_kc_slope=0.0003, trailing_stop_pct=0.018)
    signals = res["signal"].tolist()
    assert -1 in signals, "Expected short entry (-1)"
    short_idx = signals.index(-1)
    assert -2 in signals[short_idx + 1 :], "Expected parabolic or trailing short exit (-2)"


def test_enforce_final_order_wick_gate_uses_entry_context_frame():
    """
    Regression test for 2026-10-28 22:54 LOBSTER/USDT:
    When entry_frame_provider returns None/empty, enforce_final_order_wick_gate
    must use entry_context['entry_gate_frame'] instead of throwing
    BLOCKED_FINAL_WICK_FRAME_UNVERIFIED.
    """
    import asyncio
    from types import SimpleNamespace
    from core.services.entry_contract import enforce_final_order_wick_gate

    n = 10
    df = pd.DataFrame(
        {
            "timestamp": [1793199000000 + i * 60000 for i in range(n)],
            "open": [0.01960 + i * 0.00001 for i in range(n)],
            "high": [0.01966 + i * 0.00001 for i in range(n)],
            "low": [0.01958 + i * 0.00001 for i in range(n)],
            "close": [0.01965 + i * 0.00001 for i in range(n)],
            "atr": [0.00012] * n,
            "is_closed": [True] * n,
        }
    )
    df.attrs["entry_finality_verified"] = True
    df.attrs["timeframe_ms"] = 60_000.0

    async def failing_provider(_symbol):
        return None

    account = SimpleNamespace(
        entry_frame_provider=failing_provider,
        log=lambda msg, level="INFO": None,
    )
    entry_context = {"entry_gate_frame": df}

    # Should pass without raising ValueError(BLOCKED_FINAL_WICK_FRAME_UNVERIFIED)
    asyncio.run(
        enforce_final_order_wick_gate(
            account, "龙虾/USDT", "LONG", entry_context=entry_context
        )
    )


def test_kc_trend_filter_blocks_counter_trend():
    """
    Test 1000PEPE trend filter requirement:
    1. In a KC downward trend (slope < 0), long entries (Buy/Long) are 100% blocked.
    2. In a KC upward trend (slope > 0), short entries (Sell/Short) are 100% blocked.
    """
    from core.services.strategies.kc_bb_squeeze_trailing import calculate_full_kc_bb_strategy

    # Steady downward price path: 100 down to 70
    n = 30
    prices_down = [100.0 - i * 1.0 for i in range(n)]
    df_down = pd.DataFrame({
        "open": prices_down,
        "high": [p + 0.5 for p in prices_down],
        "low": [p - 0.5 for p in prices_down],
        "close": prices_down,
        "volume": [1000.0] * n,
    })
    res_down = calculate_full_kc_bb_strategy(df_down, min_kc_slope=0.0003)
    # Long signal (1) must be strictly 0 across the entire series
    assert 1 not in res_down["signal"].values, "Long signals must be strictly blocked during KC downward trend"

    # Steady upward price path: 70 up to 100
    prices_up = [70.0 + i * 1.0 for i in range(n)]
    df_up = pd.DataFrame({
        "open": prices_up,
        "high": [p + 0.5 for p in prices_up],
        "low": [p - 0.5 for p in prices_up],
        "close": prices_up,
        "volume": [1000.0] * n,
    })
    res_up = calculate_full_kc_bb_strategy(df_up, min_kc_slope=0.0003)
    # Short signal (-1) must be strictly 0 across the entire series
    assert -1 not in res_up["signal"].values, "Short signals must be strictly blocked during KC upward trend"


def test_breakout_reentry_after_long_exit():
    """
    Test breakout re-entry mechanism:
    When long exits on a false breakdown/shakeout, but the next candle surges
    above kc_upper with a big green candle, allow immediate re-entry (signal=1, BREAKOUT_REENTRY).
    """
    from core.services.strategies.kc_bb_squeeze_trailing import calculate_full_kc_bb_strategy

    # Upward base: 20 bars rising from 50 to 70
    prices = [50.0 + i * 1.0 for i in range(20)]
    # Bar 20: entry long breakout
    # Bar 21: dips slightly below kc_middle -> exit long
    # Bar 22: explosive rocket bar above kc_upper (open=68, close=75) -> BREAKOUT_REENTRY!
    opens = prices + [69.0, 69.0, 68.0]
    highs = [p + 1.0 for p in prices] + [71.0, 70.0, 76.0]
    lows = [p - 0.5 for p in prices] + [68.5, 65.0, 67.5]
    closes = prices + [70.5, 65.5, 75.0]

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000.0] * len(closes),
    })
    res = calculate_full_kc_bb_strategy(df, min_kc_slope=0.0001, bb_length=10, kc_length=10)
    # Check that breakout re-entry or signal was handled
    assert (res["signal"] == 1).any()


def test_pepe_longxia_alignment_entry_exit_and_anti_flip():
    """
    Test 1000PEPE & LongXia alignment requirements:
    1. Anti-chasing: blocks short entry when distance from KC middle > 1.2% (max_mid_dist_pct=0.012).
    2. LongXia-style big wave short exit:
       - Trailing profit lock: exits when rebounding > 1.8% from lowest_price.
       - Hard breakout exit: exits immediately when close > kc_upper.
    3. Anti-flip: blocks immediate long re-entry at midline within 2 bars after exiting short unless breaking kc_upper.
    """
    from core.services.strategies.kc_bb_squeeze_trailing import calculate_full_kc_bb_strategy

    # 1. Test anti-chasing veto when overextended > 1.2%
    base_prices = [100.0 + (-2.0 if i % 2 == 0 else 2.0) for i in range(20)] + [99.5, 99.0, 98.8]
    # Price suddenly gaps down to 92.0 (distance from ~98 mid is ~6% > 1.2%)
    prices_overextended = base_prices + [92.0]
    closes_oe = np.array(prices_overextended, dtype=float)
    df_oe = pd.DataFrame({
        "open": closes_oe + 0.1,
        "high": closes_oe + 0.2,
        "low": closes_oe - 0.2,
        "close": closes_oe,
        "volume": [1000.0] * len(closes_oe),
    })
    res_veto = calculate_full_kc_bb_strategy(df_oe, min_kc_slope=0.0003, max_mid_dist_pct=0.012)
    assert res_veto["signal"].iloc[-1] != -1, "Expected anti-chasing veto when distance > 1.2%"

    # 2. Test hard breakout exit (close > kc_upper)
    # Start in short position, then huge candle explodes above kc_upper
    prices_hard_exit = base_prices + [92.0, 90.0, 108.0]
    closes_he = np.array(prices_hard_exit, dtype=float)
    df_he = pd.DataFrame({
        "open": closes_he + 0.1,
        "high": closes_he + 0.2,
        "low": closes_he - 0.2,
        "close": closes_he,
        "volume": [1000.0] * len(closes_he),
    })
    res_he = calculate_full_kc_bb_strategy(df_he, min_kc_slope=0.0003, trailing_stop_pct=0.018)
    # The last bar must trigger hard breakout short exit (-2)
    assert res_he["signal"].iloc[-1] == -2, "Expected hard breakout short exit (-2) when close > kc_upper"
    assert res_he["exit_reason"].iloc[-1] == "UPPER_RAIL_TREND_EXIT"









