"""單元測試：驗證中軌跌破 / 死叉後的「主跌浪起點空單」及反抽寬限期放行。"""

import pandas as pd
import pytest
from core.services.math_execution_gate import validate_entry_math

def test_short_entry_at_midline_breakdown_with_buffer():
    """
    場景：龍蝦在中軌死叉後向下跌破中軌，
    K1 跌破中軌 (close 99.8 < kc_middle 100.0)；
    K2 盤中小幅反抽至 100.1 (假刺穿中軌 0.1，但在 0.15 ATR 緩衝區內)，
    收盤受阻於 MA15，MA15 處於下行狀態。
    驗證：不再被 BLOCKED_SHORT_ABOVE_MIDLINE_OR_MA15 一票否決，而是授權開空。
    """
    bars = [
        dict(timestamp=60_000, open=100.5, high=100.8, low=99.6, close=99.8,
             ma5=100.1, ma15=100.4, atr=1.0, kc_upper=104.2, kc_middle=100.2, kc_lower=96.2),
        dict(timestamp=120_000, open=99.8, high=100.2, low=99.7, close=100.05,
             ma5=99.9, ma15=100.2, atr=1.0, kc_upper=104.0, kc_middle=100.0, kc_lower=96.0),
    ]
    frame = pd.DataFrame(bars)
    allowed, reason = validate_entry_math(frame, side="SHORT")
    assert allowed is True
    assert reason in ("ENTRY_SHORT_MID_BAND_BREAK", "ENTRY_SHORT_EMA_CROSS_MID", "ENTRY_MATH_APPROVED")

def test_short_entry_clean_midline_breakdown():
    """
    場景：實體陰線明確跌破中軌，且 MA15 下行，精準放行 ENTRY_SHORT_MID_BAND_BREAK。
    """
    bars = [
        dict(timestamp=60_000, open=101.0, high=101.2, low=100.2, close=100.4,
             ma5=100.8, ma15=101.0, atr=1.0, kc_upper=104.0, kc_middle=100.2, kc_lower=96.0),
        dict(timestamp=120_000, open=100.4, high=100.5, low=99.4, close=99.5,
             ma5=99.9, ma15=100.6, atr=1.0, kc_upper=104.0, kc_middle=100.0, kc_lower=96.0),
    ]
    frame = pd.DataFrame(bars)
    allowed, reason = validate_entry_math(frame, side="SHORT")
    assert allowed is True
    assert reason == "ENTRY_SHORT_MID_BAND_BREAK"

def test_long_entry_at_midline_breakout_with_buffer():
    """
    場景：標的在中軌金叉後向上突破中軌，
    K1 突破中軌 (close 100.2 > kc_middle 99.8)；
    K2 盤中小幅回踩至 99.7 (假刺穿中軌下方 0.1，但在 0.15 ATR 緩衝區內)，
    收盤 99.95 回踩有守收紅 (open 99.8 < close 99.95)，MA15 處於上行狀態。
    驗證：不再被 BLOCKED_LONG_BELOW_MIDLINE_OR_MA15 一票否決，而是授權開多。
    """
    bars = [
        dict(timestamp=60_000, open=99.5, high=100.4, low=99.2, close=100.2,
             ma5=99.9, ma15=99.6, atr=1.0, kc_upper=104.0, kc_middle=99.8, kc_lower=96.0),
        dict(timestamp=120_000, open=99.7, high=100.05, low=99.6, close=99.95,
             ma5=100.1, ma15=99.8, atr=1.0, kc_upper=104.2, kc_middle=100.0, kc_lower=96.0),
    ]
    frame = pd.DataFrame(bars)
    allowed, reason = validate_entry_math(frame, side="LONG")
    assert allowed is True
    assert reason in ("ENTRY_LONG_MID_BAND_BREAK", "ENTRY_LONG_EMA_CROSS_MID", "ENTRY_MATH_APPROVED")

def test_long_entry_clean_midline_breakout():
    """
    場景：實體陽線明確突破中軌，且 MA15 上行，精準放行 ENTRY_LONG_MID_BAND_BREAK。
    """
    bars = [
        dict(timestamp=60_000, open=99.0, high=99.8, low=98.8, close=99.6,
             ma5=99.2, ma15=99.0, atr=1.0, kc_upper=104.0, kc_middle=99.8, kc_lower=96.0),
        dict(timestamp=120_000, open=99.6, high=100.6, low=99.5, close=100.5,
             ma5=100.1, ma15=99.4, atr=1.0, kc_upper=104.0, kc_middle=100.0, kc_lower=96.0),
    ]
    frame = pd.DataFrame(bars)
    allowed, reason = validate_entry_math(frame, side="LONG")
    assert allowed is True
    assert reason == "ENTRY_LONG_MID_BAND_BREAK"

def test_long_entry_bullish_engulfing():
    """
    場景：前一根陰線假跌破，當根大陽線強勢包覆反轉，觸發 ENTRY_LONG_ENGULFING_REVERSAL。
    """
    bars = [
        dict(timestamp=60_000, open=100.5, high=100.6, low=99.7, close=99.8,
             ma5=100.0, ma15=99.6, atr=1.0, kc_upper=104.0, kc_middle=99.9, kc_lower=96.0),
        dict(timestamp=120_000, open=99.8, high=100.8, low=99.7, close=100.7,
             ma5=100.3, ma15=99.8, atr=1.0, kc_upper=104.0, kc_middle=100.0, kc_lower=96.0),
    ]
    frame = pd.DataFrame(bars)
    allowed, reason = validate_entry_math(frame, side="LONG")
    assert allowed is True
    assert reason == "ENTRY_LONG_ENGULFING_REVERSAL"
