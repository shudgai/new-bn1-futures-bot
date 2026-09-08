import pytest
import pandas as pd
from core.engine import TradingEngine

@pytest.fixture
def engine():
    return TradingEngine()

def test_lock_profit_long(engine):
    df_lock = pd.DataFrame({
        "open": [10, 10, 10, 10], "high": [10, 10, 10, 10], "low": [10, 10, 10, 10], "close": [10, 10, 10, 10],
        "kc_upper": [12, 12, 12, 12], "kc_lower": [8, 8, 8, 8],
        "ma3": [10, 10, 10, 9], "ma15": [10, 10, 10, 10]
    })
    res1 = engine._channel_swing_action(df_lock, 9.5, "LONG", None, None, "UP", None, False, 12, 8, False, False)
    assert res1["action"] == "HOLD"
    assert res1["reason"] == "LOCK_PROFIT_LONG"

def test_unlock_profit_long(engine):
    df_unlock = pd.DataFrame({
        "open": [10, 10, 10, 10], "high": [10, 10, 10, 11], "low": [10, 10, 10, 10], "close": [10, 10, 10, 11],
        "kc_upper": [12, 12, 12, 12], "kc_lower": [8, 8, 8, 8],
        "ma3": [10, 10, 9, 11], "ma15": [10, 10, 10, 10]
    })
    res2 = engine._channel_swing_action(df_unlock, 11, "LONG", None, None, "UP", None, False, 12, 8, False, False, 1.0)
    assert res2["action"] == "HOLD"
    assert res2["reason"] == "UNLOCK_PROFIT_LONG"

def test_outer_break_lock_unlocks_on_ma_recovery_inside_channel(engine):
    df_unlock = pd.DataFrame({
        "open": [10, 10, 10, 10], "high": [10, 10, 10, 11], "low": [10, 10, 10, 10], "close": [10, 10, 10, 11],
        "kc_upper": [12, 12, 12, 12], "kc_lower": [8, 8, 8, 8],
        "ma3": [10, 10, 9, 11], "ma15": [10, 10, 10, 10]
    })
    result = engine._channel_swing_action(
        df_unlock, 11, "LONG", None, None, "UP", None, False,
        12, 8, False, 1.0,
    )
    assert result["action"] == "HOLD"
    assert result["reason"] == "UNLOCK_PROFIT_LONG"

def test_locked_long_exits_when_bearish_ma_cross_persists(engine):
    frame = pd.DataFrame({
        "open": [10, 10, 10, 10], "high": [10, 10, 10, 10], "low": [10, 10, 10, 10],
        "close": [10, 10, 10, 10], "kc_upper": [12, 12, 12, 12], "kc_lower": [8, 8, 8, 8],
        "ma3": [10, 10, 9, 9], "ma15": [10, 10, 10, 10],
    })
    result = engine._channel_swing_action(frame, 9.5, "LONG", cross_timer_start=1.0)
    assert result["action"] == "EXIT"
    assert result["reason"] == "MA3_MA15_BEARISH_EXIT_LONG"
