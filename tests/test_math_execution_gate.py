import pandas as pd

from core.services.math_execution_gate import validate_entry_math, validate_exit_math


def _frame(**current_overrides):
    bars = [
        dict(timestamp=60_000, open=100, high=101, low=99, close=100,
             ma5=100, ma15=101, atr=2, kc_upper=105, kc_middle=100,
             kc_lower=95),
        dict(timestamp=120_000, open=100, high=101, low=99, close=100,
             ma5=100, ma15=101, atr=2, kc_upper=105, kc_middle=100,
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


def test_profit_lock_fast_track_requires_matching_outer_rail_candle():
    frame = _frame(open=101, close=106, high=106.2, ma5=102, ma15=101)
    assert validate_entry_math(frame, "LONG", profit_lock_fast_track=True)[0]
    frame.loc[1, "close"] = 104
    assert not validate_entry_math(frame, "LONG", profit_lock_fast_track=True)[0]


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
