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

def test_macro_trend_hold_position():
    df = _generate_macro_frame("DOWN", 70)
    # Holding SHORT in a DOWN trend without any safety net triggers
    res = TradingEngine._channel_swing_action(df, 93.0, "SHORT")
    assert res["action"] == "HOLD"

def test_early_exit_ma15_reversal():
    df = _generate_macro_frame("DOWN", 70)
    # ma15 15 candles ago is at index 53
    # Make current ma15 strictly HIGHER than ma15 15 candles ago
    ma15_15_ago = df.loc[53, "ma15"]
    df.loc[68, "ma15"] = ma15_15_ago + 1.0 # Clearly reversed upwards
    
    res = TradingEngine._channel_swing_action(df, 93.0, "SHORT")
    assert res["action"] == "EXIT"
    assert "MA15_SHORT_TERM_REVERSAL_UP" in res["reason"]

def test_early_exit_outer_band_breakout():
    df = _generate_macro_frame("DOWN", 70)
    # Latest closed candle (index 68) breaks above kc_upper
    kc_up = df.loc[68, "kc_upper"]
    df.loc[68, "close"] = kc_up + 0.5
    
    res = TradingEngine._channel_swing_action(df, 93.0, "SHORT")
    assert res["action"] == "EXIT"
    assert "KC_UPPER_BREAKOUT_UP" in res["reason"]

def test_early_exit_structure_break():
    df = _generate_macro_frame("DOWN", 70)
    # highest high in last 20 candles is roughly 94.0
    highest_20 = df.iloc[-22:-2]["high"].max()
    # Live price suddenly spikes above highest_20
    spike_price = highest_20 + 0.5
    
    res = TradingEngine._channel_swing_action(df, spike_price, "SHORT")
    assert res["action"] == "EXIT"
    assert "STRUCTURE_BREAK_HIGH" in res["reason"]