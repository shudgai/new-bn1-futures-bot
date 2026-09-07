import pandas as pd
import pytest
from core.engine import TradingEngine

def _generate_macro_frame(trend_dir="DOWN", num_candles=70):
    df = pd.DataFrame(index=range(num_candles), columns=[
        "open", "high", "low", "close", "ma3", "ma15", "kc_upper", "kc_lower"
    ])
    
    base_price = 100.0
    for i in range(num_candles):
        if trend_dir == "DOWN":
            base_price -= 0.1
        elif trend_dir == "UP":
            base_price += 0.1
            
        df.loc[i, "ma15"] = base_price
        df.loc[i, "kc_upper"] = base_price + 1.0
        df.loc[i, "kc_lower"] = base_price - 1.0
        
        # Normal chop for ma3
        if i % 2 == 0:
            df.loc[i, "ma3"] = base_price + 0.1
        else:
            df.loc[i, "ma3"] = base_price - 0.1
            
        df.loc[i, "open"] = base_price
        df.loc[i, "close"] = base_price
        df.loc[i, "high"] = base_price + 0.2
        df.loc[i, "low"] = base_price - 0.2
        
    return df

def test_macro_trend_wait_if_insufficient_data():
    df = _generate_macro_frame(num_candles=5)
    res = TradingEngine._channel_swing_action(df, 100.0, None)
    assert res["action"] == "WAIT"

def test_macro_trend_entry_short_on_pullback():
    df = _generate_macro_frame("DOWN", 70)
    # Create a MA3 peak at iloc[-2] (the last closed candle)
    # iloc[-3] ma3 = 93.0
    # iloc[-2] ma3 = 93.5 (Peak)
    # iloc[-1] ma3 = 93.1
    df.loc[66, "ma3"] = 93.0
    df.loc[67, "ma3"] = 93.5
    df.loc[68, "ma3"] = 93.1
    df.loc[68, "close"] = 93.0
    df.loc[68, "ma15"] = 93.2
    
    res = TradingEngine._channel_swing_action(df, 93.0, None)
    assert res["action"] == "ENTER"
    assert res["side"] == "SHORT"
    assert "PEAK" in res["reason"]


def test_macro_trend_entry_short_on_lower_kc_structure_break():
    df = _generate_macro_frame("DOWN", 70)
    # The latest closed candle breaks a confirmed prior valley and the lower KC rail.
    df.loc[65, "low"] = 94.0
    df.loc[66, "low"] = 93.5
    df.loc[67, "low"] = 94.0
    df.loc[68, "close"] = 92.5
    df.loc[68, "ma3"] = 93.2
    df.loc[68, "ma15"] = 94.0
    df.loc[68, "kc_lower"] = 93.0
    df.loc[68, "low"] = 92.4

    res = TradingEngine._channel_swing_action(df, 92.5, None)

    assert res["action"] == "ENTER"
    assert res["side"] == "SHORT"
    assert res["reason"] == "KC_DOWN_TREND_LOWER_BREAKOUT"


def test_macro_trend_entry_long_on_pullback():
    df = _generate_macro_frame("UP", 70)
    # Create a MA3 trough at iloc[-2]
    df.loc[66, "ma3"] = 107.0
    df.loc[67, "ma3"] = 106.5
    df.loc[68, "ma3"] = 106.9
    df.loc[68, "close"] = 107.0
    df.loc[68, "ma15"] = 106.8
    
    res = TradingEngine._channel_swing_action(df, 107.0, None)
    assert res["action"] == "ENTER"
    assert res["side"] == "LONG"
    assert "TROUGH" in res["reason"]


def test_macro_trend_entry_long_on_upper_kc_structure_break():
    df = _generate_macro_frame("UP", 70)
    # The latest closed candle breaks a confirmed prior peak and the upper KC rail.
    df.loc[65, "high"] = 106.0
    df.loc[66, "high"] = 106.5
    df.loc[67, "high"] = 106.0
    df.loc[68, "close"] = 107.5
    df.loc[68, "ma3"] = 107.2
    df.loc[68, "ma15"] = 106.5
    df.loc[68, "kc_upper"] = 107.0
    df.loc[68, "high"] = 107.6

    res = TradingEngine._channel_swing_action(df, 107.5, None)

    assert res["action"] == "ENTER"
    assert res["side"] == "LONG"
    assert res["reason"] == "KC_UP_TREND_UPPER_BREAKOUT"


def test_macro_trend_hold_position():
    df = _generate_macro_frame("DOWN", 70)
    # Holding SHORT in a DOWN trend without any safety net triggers
    res = TradingEngine._channel_swing_action(df, 93.0, "SHORT")
    assert res["action"] == "HOLD"


def test_long_reverses_short_on_red_candle_below_lower_kc_with_downtrend():
    df = _generate_macro_frame("DOWN", 70)
    df.loc[68, ["open", "close", "kc_lower"]] = [93.8, 92.5, 93.0]

    res = TradingEngine._channel_swing_action(df, 92.5, "LONG")

    assert res["action"] == "REVERSE"
    assert res["side"] == "SHORT"
    assert res["reason"] == "KC_LOWER_RED_REVERSE_SHORT"


def test_long_holds_on_red_candle_below_lower_kc_without_downtrend():
    df = _generate_macro_frame("UP", 70)
    df.loc[68, ["open", "close", "kc_lower"]] = [107.0, 105.5, 106.0]

    res = TradingEngine._channel_swing_action(df, 105.5, "LONG")

    assert res["action"] == "HOLD"
    assert res["reason"] != "KC_LOWER_RED_REVERSE_SHORT"


def test_short_reverses_long_on_green_candle_above_upper_kc_with_uptrend():
    df = _generate_macro_frame("UP", 70)
    df.loc[68, ["open", "close", "kc_upper"]] = [106.2, 107.5, 107.0]

    res = TradingEngine._channel_swing_action(df, 107.5, "SHORT")

    assert res["action"] == "REVERSE"
    assert res["side"] == "LONG"
    assert res["reason"] == "KC_UPPER_GREEN_REVERSE_LONG"


def test_short_holds_on_green_candle_above_upper_kc_without_uptrend():
    df = _generate_macro_frame("DOWN", 70)
    df.loc[68, ["open", "close", "kc_upper"]] = [93.0, 95.0, 94.0]

    res = TradingEngine._channel_swing_action(df, 95.0, "SHORT")

    assert res["action"] == "HOLD"
    assert res["reason"] != "KC_UPPER_GREEN_REVERSE_LONG"


def test_long_waits_for_net_profit_before_outer_reversal():
    df = _generate_macro_frame("DOWN", 70)
    df.loc[68, ["open", "close", "kc_lower"]] = [93.8, 92.5, 93.0]

    res = TradingEngine._channel_swing_action(
        df, 92.5, "LONG", exit_net_profitable=False,
    )

    assert res["action"] == "REVERSE"
    assert res["side"] == "SHORT"
    assert res["reason"] == "KC_LOWER_RED_REVERSE_SHORT"


def test_long_locks_profit_when_red_candle_returns_inside_lower_rail():
    df = _generate_macro_frame("UP", 70)
    df.loc[68, ["open", "close", "ma3", "ma15", "kc_middle", "kc_lower"]] = [107.0, 106.5, 106.8, 106.7, 106.8, 105.5]
    res = TradingEngine._channel_swing_action(df, 106.5, "LONG")
    assert res["action"] == "HOLD"
    assert res["reason"] == "LOCK_PROFIT_LONG"


def test_short_locks_profit_when_green_candle_returns_inside_upper_rail():
    df = _generate_macro_frame("DOWN", 70)
    df.loc[68, ["open", "close", "ma3", "ma15", "kc_middle", "kc_upper"]] = [93.0, 93.5, 93.2, 93.3, 93.2, 94.5]
    res = TradingEngine._channel_swing_action(df, 93.5, "SHORT")
    assert res["action"] == "HOLD"
    assert res["reason"] == "LOCK_PROFIT_SHORT"


def test_long_unlocks_profit_only_after_bullish_ma_cross_above_middle():
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["ma3", "ma15", "close", "kc_middle"]] = [106.0, 106.5, 106.4, 106.2]
    df.loc[68, ["ma3", "ma15", "close", "kc_middle"]] = [107.0, 106.5, 107.0, 106.7]

    res = TradingEngine._channel_swing_action(df, 107.0, "LONG")

    assert res["action"] == "HOLD"
    assert res["reason"] == "UNLOCK_PROFIT_LONG"


def test_short_unlocks_profit_only_after_bearish_ma_cross_below_middle():
    df = _generate_macro_frame("DOWN", 70)
    df.loc[67, ["ma3", "ma15", "close", "kc_middle"]] = [93.0, 92.5, 92.6, 92.8]
    df.loc[68, ["ma3", "ma15", "close", "kc_middle"]] = [92.0, 92.5, 92.0, 92.3]

    res = TradingEngine._channel_swing_action(df, 92.0, "SHORT")

    assert res["action"] == "HOLD"
    assert res["reason"] == "UNLOCK_PROFIT_SHORT"


def test_short_waits_for_net_profit_before_outer_reversal():
    df = _generate_macro_frame("UP", 70)
    df.loc[68, ["open", "close", "kc_upper"]] = [106.2, 107.5, 107.0]

    res = TradingEngine._channel_swing_action(
        df, 107.5, "SHORT", exit_net_profitable=False,
    )

    assert res["action"] == "REVERSE"
    assert res["side"] == "LONG"
    assert res["reason"] == "KC_UPPER_GREEN_REVERSE_LONG"


def test_long_locks_profit_on_strong_ma3_ma15_bearish_cross():
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["ma3", "ma15", "close", "open", "kc_upper", "kc_lower"]] = [106.5, 106.0, 106.4, 104.5, 107.0, 104.0]
    df.loc[68, ["ma3", "ma15", "close", "open", "kc_upper", "kc_lower"]] = [105.5, 106.0, 104.5, 104.5, 107.0, 104.0]

    res = TradingEngine._channel_swing_action(df, 104.5, "LONG")

    assert res["action"] == "HOLD"
    assert res["reason"] == "LOCK_PROFIT_LONG"


def test_short_locks_profit_on_strong_ma3_ma15_bullish_cross():
    df = _generate_macro_frame("DOWN", 70)
    df.loc[67, ["ma3", "ma15", "close", "kc_upper", "kc_lower"]] = [92.5, 93.0, 93.0, 95.0, 92.0]
    df.loc[68, ["ma3", "ma15", "close", "kc_upper", "kc_lower"]] = [94.5, 94.0, 94.6, 95.0, 92.0]

    res = TradingEngine._channel_swing_action(df, 94.6, "SHORT")

    assert res["action"] == "HOLD"
    assert res["reason"] == "LOCK_PROFIT_SHORT"


def test_macro_trend_requires_30_and_60_bar_staircase():
    df = _generate_macro_frame("DOWN", 70)
    # Break the 30-bar step while keeping the short-term slope downward.
    df.loc[38, "ma15"] = 92.0

    res = TradingEngine._channel_swing_action(df, 93.0, None)

    assert res["action"] == "WAIT"


def test_short_holds_on_green_candle_breaking_upper_kc_against_downtrend():
    df = _generate_macro_frame("DOWN", 70)
    df.loc[68, "close"] = 94.2

    res = TradingEngine._channel_swing_action(df, 94.2, "SHORT")

    assert res["action"] == "HOLD"
    assert res["reason"] != "KC_UPPER_GREEN_REVERSE_LONG"


def test_short_does_not_reverse_when_green_candle_is_already_outside_upper_kc():
    df = _generate_macro_frame("DOWN", 70)
    df.loc[67, ["close", "kc_upper"]] = [95.0, 94.0]
    df.loc[68, ["open", "close", "kc_upper"]] = [95.0, 95.5, 94.0]

    res = TradingEngine._channel_swing_action(df, 95.5, "SHORT")

    assert res["action"] == "HOLD"
    assert res["reason"] != "KC_UPPER_GREEN_REVERSE_LONG"


def test_long_does_not_reverse_on_red_wick_without_lower_kc_close():
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["close", "kc_lower"]] = [105.0, 106.0]
    df.loc[68, ["open", "close", "low", "kc_lower"]] = [105.0, 106.5, 105.5, 106.0]

    res = TradingEngine._channel_swing_action(df, 106.5, "LONG")

    assert res["action"] == "HOLD"
    assert res["reason"] != "KC_LOWER_RED_REVERSE_SHORT"


def test_live_outer_entry_follows_directional_short_continuation():
    df = _generate_macro_frame("DOWN", 70)
    df.loc[66, ["close", "kc_lower"]] = [93.1, 93.0]
    df.loc[67, ["close", "kc_lower"]] = [92.8, 92.9]
    df.loc[68, ["open", "close", "low", "kc_lower"]] = [92.8, 92.5, 92.4, 92.7]
    df.loc[69, ["open", "close", "kc_lower"]] = [92.5, 92.3, 92.6]

    res = TradingEngine._channel_live_outer_entry_action(df, 92.3)

    assert res["action"] == "ENTER"
    assert res["side"] == "SHORT"
    assert res["reason"] == "KC_LIVE_LOWER_BREAK_SHORT"


def test_long_exits_when_price_breaks_lower_kc_without_crash():
    df = _generate_macro_frame("UP", 70)
    df.loc[68, "close"] = 106.5

    res = TradingEngine._channel_swing_action(df, 105.5, "LONG")

    assert res["action"] == "HOLD"
    assert res["reason"] == "WAIT_RED_CANDLE_BELOW_LOWER_KC"


def test_long_exits_immediately_on_waterfall():
    df = _generate_macro_frame("UP", 70)

    res = TradingEngine._channel_swing_action(df, 90.0, "LONG")

    assert res["action"] == "EXIT"
    assert res["reason"] == "EMERGENCY_EXIT_WATERFALL_DOWN"


def test_long_holds_through_same_direction_waterfall_up():
    df = _generate_macro_frame("UP", 70)
    df.loc[68, ["open", "close", "high", "kc_upper"]] = [107.0, 110.0, 110.5, 108.0]

    res = TradingEngine._channel_swing_action(df, 110.0, "LONG")

    assert res["action"] == "HOLD"
    assert res["reason"] != "EMERGENCY_EXIT_WATERFALL_UP"


def test_short_holds_through_same_direction_waterfall_down():
    df = _generate_macro_frame("DOWN", 70)
    df.loc[68, ["open", "close", "low", "kc_lower"]] = [93.0, 90.0, 89.5, 92.0]

    res = TradingEngine._channel_swing_action(df, 90.0, "SHORT")

    assert res["action"] == "HOLD"
    assert res["reason"] != "EMERGENCY_EXIT_WATERFALL_DOWN"


def test_long_exits_immediately_when_two_candles_cross_kc():
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["open", "close"]] = [107.2, 106.7]
    df.loc[68, ["open", "close"]] = [106.8, 105.5]

    res = TradingEngine._channel_swing_action(df, 105.5, "LONG")

    assert res["action"] == "EXIT"
    assert res["reason"] == "EMERGENCY_EXIT_2_CANDLE_CRASH"

def test_inflection_point_entry_long():
    df = _generate_macro_frame("DOWN", 70) # Was going down
    # Force a U-shape bottom
    # 30 ago (index 38) -> 15 ago (index 53) -> current (index 68)
    df.loc[38, "ma15"] = 96.2
    df.loc[53, "ma15"] = 94.7 # dropped
    df.loc[68, "ma15"] = 95.0 # started curling up (95.0 > 94.7)
    
    # Ensure alignment filter passes (close > ma3 > ma15)
    df.loc[68, "close"] = 95.5
    df.loc[68, "ma3"] = 95.2
    
    res = TradingEngine._channel_swing_action(df, 95.5, None)
    assert res["action"] == "ENTER"
    assert res["side"] == "LONG"
    assert "U_SHAPE" in res["reason"]

def test_inflection_point_entry_short():
    df = _generate_macro_frame("UP", 70) # Was going up
    # Force an inverted U-shape top
    # 30 ago (index 38) -> 15 ago (index 53) -> current (index 68)
    df.loc[38, "ma15"] = 103.8
    df.loc[53, "ma15"] = 105.3 # rose
    df.loc[68, "ma15"] = 105.0 # started curving down (105.0 < 105.3)
    
    # Ensure alignment filter passes (close < ma3 < ma15)
    df.loc[68, "close"] = 104.5
    df.loc[68, "ma3"] = 104.8
    
    res = TradingEngine._channel_swing_action(df, 104.5, None)
    assert res["action"] == "ENTER"
    assert res["side"] == "SHORT"
    assert "INVERTED_U_TOP" in res["reason"]