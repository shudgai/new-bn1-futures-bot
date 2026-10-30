"""
Unit tests for ScalpingStateMachine.
Verify all 3 States: SHORT, LONG, FLAT
and state transitions:
1. SHORT -> CLOSE_SHORT_AND_OPEN_LONG (Bullish Macro V-Reversal)
2. SHORT -> CLOSE_SHORT (Bearish Macro)
3. SHORT -> HOLD (No breach of MA15)
4. LONG -> CLOSE_LONG_AND_OPEN_SHORT (Bearish Macro breakdown)
5. LONG -> CLOSE_LONG (Bullish Macro breakdown)
6. LONG -> HOLD (Holding above MA15)
7. FLAT -> OPEN_LONG (Bullish Macro + MA3 > MA15 + Price >= MA15)
8. FLAT -> OPEN_SHORT (Bearish Macro + MA3 < MA15 + Price <= MA15)
9. FLAT -> WAIT (Counter-trend or unaligned)
"""
import pytest
from core.services.scalping_state_machine import ScalpingStateMachine

def test_short_v_reversal_bullish_flip():
    action, reason = ScalpingStateMachine.evaluate(
        position_state="SHORT",
        macro_trend="BULLISH",
        current_price=105.0,
        ma3=104.0,
        ma15=102.0,  # Price 105 > MA15 102
        entry_price=110.0,
    )
    assert action == "CLOSE_SHORT_AND_OPEN_LONG"
    assert "一鍵翻單" in reason

def test_short_v_reversal_bearish_close():
    action, reason = ScalpingStateMachine.evaluate(
        position_state="SHORT",
        macro_trend="BEARISH",
        current_price=105.0,
        ma3=104.0,
        ma15=102.0,  # Price 105 > MA15 102
        entry_price=110.0,
    )
    assert action == "CLOSE_SHORT"

def test_short_hold():
    action, reason = ScalpingStateMachine.evaluate(
        position_state="SHORT",
        macro_trend="BEARISH",
        current_price=100.0,
        ma3=99.0,
        ma15=102.0,  # Price 100 < MA15 102
        entry_price=110.0,
    )
    assert action == "HOLD"

def test_long_breakdown_bearish_flip():
    action, reason = ScalpingStateMachine.evaluate(
        position_state="LONG",
        macro_trend="BEARISH",
        current_price=98.0,
        ma3=99.0,
        ma15=101.0,  # Price 98 < MA15 101
        entry_price=95.0,
    )
    assert action == "CLOSE_LONG_AND_OPEN_SHORT"
    assert "一鍵翻單" in reason

def test_long_breakdown_bullish_close():
    action, reason = ScalpingStateMachine.evaluate(
        position_state="LONG",
        macro_trend="BULLISH",
        current_price=98.0,
        ma3=99.0,
        ma15=101.0,  # Price 98 < MA15 101
        entry_price=95.0,
    )
    assert action == "CLOSE_LONG"

def test_long_hold():
    action, reason = ScalpingStateMachine.evaluate(
        position_state="LONG",
        macro_trend="BULLISH",
        current_price=105.0,
        ma3=106.0,
        ma15=102.0,  # Price 105 > MA15 102
        entry_price=100.0,
    )
    assert action == "HOLD"

def test_flat_bullish_reentry():
    action, reason = ScalpingStateMachine.evaluate(
        position_state="FLAT",
        macro_trend="BULLISH",
        current_price=105.0,
        ma3=104.0,
        ma15=102.0,  # MA3 104 > MA15 102, Price 105 >= MA15 102
    )
    assert action == "OPEN_LONG"
    assert "開多" in reason

def test_flat_bearish_reentry():
    action, reason = ScalpingStateMachine.evaluate(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=98.0,
        ma3=97.0,
        ma15=100.0,  # MA3 97 < MA15 100, Price 98 <= MA15 100
    )
    assert action == "OPEN_SHORT"
    assert "開空" in reason

def test_flat_wait_mismatch():
    action, reason = ScalpingStateMachine.evaluate(
        position_state="FLAT",
        macro_trend="BEARISH",
        current_price=105.0,
        ma3=104.0,
        ma15=102.0,  # Counter-trend: Bearish macro but MA3 > MA15
    )
    assert action == "WAIT"

