import pandas as pd

from core.services.math_execution_gate import validate_entry_math, validate_exit_math


def _frame(**current_overrides):
    bars = [
        dict(timestamp=60_000, open=100, high=101, low=99, close=100,
             ma5=100, ma15=101, atr=2, kc_upper=105, kc_middle=100,
             kc_lower=95),
        dict(timestamp=120_000, open=100, high=101, low=99, close=100,
             ma5=100, ma15=101, atr=2, kc_upper=105, kc_middle=101,
             kc_lower=95),
    ]
    bars[-2].update(ma5=99)
    bars[-1].update(current_overrides)
    return pd.DataFrame(bars)


def test_entry_math_alignment_chase_and_wick_veto():
    frame = _frame(ma5=102, ma15=101, close=102, open=101.5, high=102.2)
    assert validate_entry_math(frame, "LONG")[0]
    frame.loc[1, ["close", "high"]] = [104, 104.2]
    assert validate_entry_math(frame, "LONG")[1] == "BLOCKED_ENTRY_MATH_LONG_CHASE"
    frame = _frame(ma5=102, ma15=101, close=102, open=101.5, high=103)
    assert validate_entry_math(frame, "LONG")[1] == "BLOCKED_ENTRY_MATH_LONG_WICK"


def test_outer_rail_breakout_and_continuation_allows_entry():
    # Outer rail long breakout: close > kc_upper (107 > 105), distance <= 3 ATR
    frame = _frame(ma5=106, ma15=101, close=107, open=105.5, high=107.2, kc_upper=105, kc_middle=101, atr=2)
    assert validate_entry_math(frame, "LONG") == (True, "ENTRY_MATH_APPROVED")

    # Long outer chase over 3 ATR: close - kc_upper = 112 - 105 = 7 > 3 * 2 (6)
    frame.loc[1, ["close", "high"]] = [112, 112.2]
    assert validate_entry_math(frame, "LONG") == (False, "BLOCKED_ENTRY_MATH_LONG_CHASE")

    # Long outer with MA5 downturn
    frame.loc[1, ["close", "high", "ma5"]] = [107, 107.2, 98]
    assert validate_entry_math(frame, "LONG") == (False, "BLOCKED_ENTRY_MATH_LONG_MA_ALIGNMENT")

    # Outer rail short breakout: close < kc_lower (93 < 95), distance <= 3 ATR
    short_frame = _frame(ma5=94, ma15=99, close=93, open=94.5, low=92.8, high=94.6, kc_lower=95, kc_middle=99, atr=2)
    short_frame.loc[0, "kc_middle"] = 100
    assert validate_entry_math(short_frame, "SHORT") == (True, "ENTRY_MATH_APPROVED")

    # Short outer with MA5 >= MA15 (countertrend MA alignment)
    short_bad_ma = short_frame.copy()
    short_bad_ma.loc[1, "ma5"] = 99.5
    assert validate_entry_math(short_bad_ma, "SHORT") == (False, "BLOCKED_ENTRY_MATH_SHORT_MA_ALIGNMENT")

    # Short outer with MA5 and MA15 glued together (Chop)
    short_chop = short_frame.copy()
    short_chop.loc[1, ["ma5", "ma15"]] = [94.02, 94.0]
    assert validate_entry_math(short_chop, "SHORT") == (False, "BLOCKED_ENTRY_MATH_MA_TOO_CLOSE")


def test_profit_lock_fast_track_requires_matching_outer_rail_candle():
    frame = _frame(open=101, close=106, high=106.2, ma5=102, ma15=101)
    assert validate_entry_math(frame, "LONG", profit_lock_fast_track=True)[0]
    frame.loc[1, "close"] = 104
    assert not validate_entry_math(frame, "LONG", profit_lock_fast_track=True)[0]


def test_kc_midline_trend_vetoes_countertrend_and_cannot_be_bypassed():
    bearish = _frame(
        open=100, high=101, low=99, close=100,
        ma5=102, ma15=101, kc_middle=99,
    )
    assert validate_entry_math(bearish, "LONG") == (
        False, "BLOCKED_LONG_ON_BEARISH_TREND",
    )
    assert validate_entry_math(
        bearish, "LONG", profit_lock_fast_track=True,
    ) == (False, "BLOCKED_LONG_ON_BEARISH_TREND")

    bullish = _frame(
        open=100, high=101, low=99, close=100,
        ma5=98, ma15=99, kc_middle=101,
    )
    assert validate_entry_math(bullish, "SHORT") == (
        False, "BLOCKED_SHORT_ON_BULLISH_TREND",
    )

    flat = _frame(kc_middle=100)
    assert validate_entry_math(flat, "LONG")[1] == "BLOCKED_ENTRY_MATH_KC_TREND_FLAT"


def test_execution_entry_validation_prefers_fresh_market_frame_over_signal_cache():
    import asyncio

    from core.services.math_execution_gate import validate_entry_at_execution

    stale_bullish = _frame(
        open=100, high=101, low=99, close=100,
        ma5=102, ma15=101, kc_middle=101,
    )
    fresh_bearish = _frame(
        open=100, high=101, low=99, close=100,
        ma5=102, ma15=101, kc_middle=99,
    )

    class Account:
        entry_frame_provider = staticmethod(lambda _symbol: fresh_bearish)

    allowed, reason = asyncio.run(validate_entry_at_execution(
        Account(), "CAP/USDT", "LONG", {"frame": stale_bullish},
    ))
    assert not allowed
    assert reason == "BLOCKED_LONG_ON_BEARISH_TREND"


def test_exit_math_true_peak_and_middle_trend_hold_veto():
    position = dict(side="LONG", entry_price=100, qty=1, margin=100,
                    highest_price=106, lowest_price=100)
    frame = _frame(open=105, high=106, low=104, close=105,
                   ma5=104, atr=2, kc_upper=105, kc_middle=103,
                   kc_lower=95)
    allowed, _, metadata = validate_exit_math(position, frame, 104.5)
    assert allowed and metadata["exit_type"] == "TRUE_PEAK"
    frame.loc[1, "kc_upper"] = 107
    allowed, reason, metadata = validate_exit_math(position, frame, 105)
    assert not allowed and reason == "VETO_HOLD"
    assert metadata["exit_type"] == "VETO_HOLD"


def test_exit_math_ratchet_and_emergency_override():
    position = dict(side="LONG", entry_price=100, qty=1, margin=100,
                    highest_price=110, lowest_price=100,
                    profit_lock_state={"peak_net_roe_pct": 10, "floor_net_roe_pct": 7})
    frame = _frame(open=109, high=110, low=107, close=109,
                   ma5=108, atr=2, kc_upper=112, kc_middle=105,
                   kc_lower=95)
    allowed, _, metadata = validate_exit_math(position, frame, 106)
    assert allowed and metadata["exit_type"] == "PROFIT_LOCK"
    allowed, reason, metadata = validate_exit_math(position, frame, 90,
                                                   close_reason="CIRCUIT_BREAKER_HARD_SL")
    assert allowed and reason == "EMERGENCY_EXIT_OVERRIDE"
    assert metadata["exit_type"] == "EMERGENCY"
