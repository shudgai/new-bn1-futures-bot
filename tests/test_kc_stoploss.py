import pytest
import pandas as pd
from core.indicators import compute_position_trigger
from core.config import KELTNER_ATR_MULTIPLIER

def test_kc_bands_in_trigger():
    df = pd.DataFrame({
        'close': [100.0] * 15,
        'open': [100.0] * 15,
        'high': [100.0] * 15,
        'low': [100.0] * 15,
        'volume': [10.0] * 15,
        'ma3': [100.0] * 15,
        'ma5': [100.0] * 15,
        'ema_20': [100.0] * 15,
        'atr': [2.0] * 15,
        'kc_upper': [102.0] * 15,
        'kc_lower': [98.0] * 15,
    })
    trigger = compute_position_trigger(df, "LONG")
    assert trigger["kc_upper"] == 102.0
    assert trigger["kc_lower"] == 98.0

def test_kc_bands_calculated_if_missing():
    df = pd.DataFrame({
        'close': [100.0] * 15,
        'open': [100.0] * 15,
        'high': [100.0] * 15,
        'low': [100.0] * 15,
        'volume': [10.0] * 15,
        'ma3': [100.0] * 15,
        'ma5': [100.0] * 15,
        'ema_20': [100.0] * 15,
        'atr': [2.0] * 15,
    })
    trigger = compute_position_trigger(df, "LONG")
    expected_upper = 100.0 + 2.0 * KELTNER_ATR_MULTIPLIER
    expected_lower = 100.0 - 2.0 * KELTNER_ATR_MULTIPLIER
    assert abs(trigger["kc_upper"] - expected_upper) < 1e-6
    assert abs(trigger["kc_lower"] - expected_lower) < 1e-6

if __name__ == '__main__':
    pytest.main(['-v', __file__])
