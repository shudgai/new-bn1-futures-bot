import pytest
import pandas as pd
from core.engine import TradingEngine

@pytest.fixture
def engine():
    return object.__new__(TradingEngine)

def test_ma_cross_does_not_arm_removed_profit_lock(engine):
    df_lock = pd.DataFrame({
        "open": [10, 10, 10, 10], "high": [10, 10, 10, 10], "low": [10, 10, 10, 10], "close": [10, 10, 10, 10],
        "kc_upper": [12, 12, 12, 12], "kc_lower": [8, 8, 8, 8],
        "ma3": [10, 10, 10, 9], "ma15": [10, 10, 10, 10]
    })
    res1 = engine._channel_swing_action(df_lock, 9.5, "LONG", None, None, "UP", None, False, 12, 8, False, False)
    assert res1["action"] == "HOLD"
    assert res1["reason"] == "HOLDING_LONG_RUN_TO_HIGH"

def test_ma_recovery_holds_without_removed_unlock_signal(engine):
    df_unlock = pd.DataFrame({
        "open": [10, 10, 10, 10], "high": [10, 10, 10, 11], "low": [10, 10, 10, 10], "close": [10, 10, 10, 11],
        "kc_upper": [12, 12, 12, 12], "kc_lower": [8, 8, 8, 8],
        "ma3": [10, 10, 9, 11], "ma15": [10, 10, 10, 10]
    })
    res2 = engine._channel_swing_action(df_unlock, 11, "LONG", None, None, "UP", None, False, 12, 8, False, False, 1.0)
    assert res2["action"] == "HOLD"
    assert res2["reason"] == "HOLDING_LONG_RUN_TO_HIGH"

def test_legacy_lock_argument_does_not_restore_unlock_signal(engine):
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
    assert result["reason"] == "HOLDING_LONG_RUN_TO_HIGH"

def test_bearish_ma_cross_alone_does_not_exit(engine):
    frame = pd.DataFrame({
        "open": [10, 10, 10, 10], "high": [10, 10, 10, 10], "low": [10, 10, 10, 10],
        "close": [10, 10, 10, 10], "kc_upper": [12, 12, 12, 12], "kc_lower": [8, 8, 8, 8],
        "ma3": [10, 10, 9, 9], "ma15": [10, 10, 10, 10],
    })
    result = engine._channel_swing_action(frame, 9.5, "LONG", cross_timer_start=1.0)
    assert result["action"] == "HOLD"
    assert result["reason"] == "HOLDING_LONG_RUN_TO_HIGH"
