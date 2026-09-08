import pytest
import pandas as pd
from core.engine import TradingEngine

@pytest.fixture
def engine():
    return TradingEngine()

def test_lock_profit_long(engine):
    df_lock = pd.DataFrame({
        "open": [10, 10], "high": [10, 10], "low": [10, 10], "close": [10, 10],
        "kc_upper": [12, 12], "kc_lower": [8, 8],
        "ma3": [10, 9], "ma15": [10, 10]
    })
    res1 = engine._channel_swing_action(df_lock, 9.5, "LONG", None, None, "UP", None, False, 12, 8, False, False)
    assert res1["action"] == "HOLD"
    assert res1["reason"] == "LOCK_PROFIT_LONG"

def test_unlock_profit_long(engine):
    df_unlock = pd.DataFrame({
        "open": [10, 10], "high": [10, 11], "low": [10, 10], "close": [10, 11],
        "kc_upper": [12, 12], "kc_lower": [8, 8],
        "ma3": [9, 11], "ma15": [10, 10]
    })
    res2 = engine._channel_swing_action(df_unlock, 11, "LONG", None, None, "UP", None, False, 12, 8, False, True)
    assert res2["action"] == "HOLD"
    assert res2["reason"] == "UNLOCK_PROFIT_LONG"

def test_outer_break_lock_unlocks_on_ma_recovery_inside_channel(engine):
    df_unlock = pd.DataFrame({
        "open": [10, 10], "high": [10, 11], "low": [10, 10], "close": [10, 11],
        "kc_upper": [12, 12], "kc_lower": [8, 8],
        "ma3": [9, 11], "ma15": [10, 10]
    })
    result = engine._channel_swing_action(
        df_unlock, 11, "LONG", None, None, "UP", None, False,
        12, 8, True, True,
    )
    assert result["action"] == "HOLD"
    assert result["reason"] == "UNLOCK_PROFIT_LONG"
