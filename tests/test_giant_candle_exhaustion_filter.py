"""Unit tests verifying symmetric giant candle and price-too-low / price-too-high protection."""

import pandas as pd
import pytest
from core.services import mtf_kc_state_machine as mtf


def test_short_on_giant_dump_candle_is_blocked_as_price_too_low():
    closed = pd.DataFrame([{
        "open": 0.0215, "high": 0.0216, "low": 0.0209, "close": 0.0210,
        "kc_middle": 0.0210, "kc_upper": 0.0215, "kc_lower": 0.0205,
        "atr": 0.00025, "timestamp": 60000,
    }])
    blocked, reason = mtf._is_giant_or_exhausted_candle(
        closed=closed, side="SHORT", quote_value=0.0204, open_price=0.0210,
        live_high=0.02104, live_low=0.02015, middle=0.0209, half_width=0.0004,
        ma15_current=0.0210, previous_atr=0.00025,
    )
    assert blocked is True
    assert "PRICE_TOO_LOW" in reason


def test_short_on_counter_trend_bull_candle_is_blocked():
    closed = pd.DataFrame([{
        "open": 0.0210, "high": 0.0211, "low": 0.0208, "close": 0.0210,
        "kc_middle": 0.0210, "kc_upper": 0.0215, "kc_lower": 0.0205,
        "atr": 0.00025, "timestamp": 60000,
    }])
    blocked, reason = mtf._is_giant_or_exhausted_candle(
        closed=closed, side="SHORT", quote_value=0.0214, open_price=0.0210,
        live_high=0.0214, live_low=0.0210, middle=0.0210, half_width=0.0004,
        ma15_current=0.0210, previous_atr=0.00025,
    )
    assert blocked is True
    assert "BLOCKED_COUNTER_BULL_CANDLE_SHORT" in reason


def test_long_on_giant_pump_candle_is_blocked_as_price_too_high():
    closed = pd.DataFrame([{
        "open": 0.0205, "high": 0.0211, "low": 0.0204, "close": 0.0210,
        "kc_middle": 0.0210, "kc_upper": 0.0215, "kc_lower": 0.0205,
        "atr": 0.00025, "timestamp": 60000,
    }])
    blocked, reason = mtf._is_giant_or_exhausted_candle(
        closed=closed, side="LONG", quote_value=0.0216, open_price=0.0210,
        live_high=0.0216, live_low=0.0210, middle=0.0210, half_width=0.0004,
        ma15_current=0.0210, previous_atr=0.00025,
    )
    assert blocked is True
    assert "PRICE_TOO_HIGH" in reason


def test_long_on_counter_trend_bear_candle_is_blocked():
    closed = pd.DataFrame([{
        "open": 0.0210, "high": 0.0211, "low": 0.0208, "close": 0.0210,
        "kc_middle": 0.0210, "kc_upper": 0.0215, "kc_lower": 0.0205,
        "atr": 0.00025, "timestamp": 60000,
    }])
    blocked, reason = mtf._is_giant_or_exhausted_candle(
        closed=closed, side="LONG", quote_value=0.0207, open_price=0.0210,
        live_high=0.0210, live_low=0.0207, middle=0.0210, half_width=0.0004,
        ma15_current=0.0210, previous_atr=0.00025,
    )
    assert blocked is True
    assert "BLOCKED_COUNTER_BEAR_CANDLE_LONG" in reason
