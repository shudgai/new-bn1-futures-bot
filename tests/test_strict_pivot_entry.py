import pandas as pd

from core.engine import TradingEngine
from core.strategy import detect_simple_ma5_signal


def _frame(side: str) -> pd.DataFrame:
    if side == "LONG":
        return pd.DataFrame({
            "open": [100.0, 99.0, 97.9, 98.8],
            "close": [99.5, 98.5, 98.2, 99.4],
            "high": [102.5, 99.2, 98.7, 99.6],
            "low": [99.3, 98.2, 97.8, 98.7],
            "ma3": [100.0, 97.0, 98.0, 99.0],
            "ma15": [101.0] * 4,
            "ema_20": [100.0] * 4,
            "kc_upper": [102.0] * 4,
            "kc_lower": [98.0] * 4,
            "atr": [1.0] * 4,
        })
    return pd.DataFrame({
        "open": [100.0, 101.0, 102.0, 101.2],
        "close": [100.5, 101.5, 101.8, 100.6],
        "high": [100.7, 101.8, 102.2, 101.3],
        "low": [97.5, 100.8, 101.3, 100.4],
        "ma3": [100.0, 103.0, 102.0, 101.0],
        "ma15": [99.0] * 4,
        "ema_20": [100.0] * 4,
        "kc_upper": [102.0] * 4,
        "kc_lower": [98.0] * 4,
        "atr": [1.0] * 4,
    })












def test_entry_atr_uses_candle_atr_when_signal_payload_is_missing_it():
    frame = _frame("SHORT")
    atr = TradingEngine._resolve_entry_atr({}, frame, live_price=100.0)
    assert atr == 1.0






def test_short_prealert_is_suppressed_near_lower_rail():
    frame = pd.DataFrame({
        "open": [101.0, 100.2, 98.4],
        "close": [100.5, 99.2, 97.9],
        "ma3": [102.3, 102.2, 98.2],
        "ema_20": [100.0] * 3,
        "kc_upper": [102.0] * 3,
        "kc_lower": [98.0] * 3,
        "atr": [1.0] * 3,
    })
    assert TradingEngine._detect_strict_pivot_prealert(frame) is None


def test_prealert_waits_for_ma3_turn_not_only_candle_color():
    frame = pd.DataFrame({
        "open": [101.0, 101.4, 101.5],
        "close": [101.2, 101.6, 101.3],
        "ma3": [102.3, 102.3, 102.3],
        "ema_20": [100.0] * 3,
        "kc_upper": [102.0] * 3,
        "kc_lower": [98.0] * 3,
        "atr": [1.0] * 3,
    })
    assert TradingEngine._detect_strict_pivot_prealert(frame) is None




















