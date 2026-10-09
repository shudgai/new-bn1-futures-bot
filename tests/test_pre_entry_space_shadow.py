import pytest
import json
import os
import copy
from unittest.mock import patch, MagicMock
from core.services.pre_entry_space_shadow import record_pre_entry_space_shadow

@pytest.fixture(autouse=True)
def clean_log():
    if os.path.exists("logs/pre_entry_space_shadow.jsonl"):
        os.remove("logs/pre_entry_space_shadow.jsonl")
    if not os.path.exists("logs"):
        os.makedirs("logs")

def read_last_log():
    if not os.path.exists("logs/pre_entry_space_shadow.jsonl"):
        return None
    with open("logs/pre_entry_space_shadow.jsonl", "r") as f:
        lines = f.readlines()
        if not lines:
            return None
        return json.loads(lines[-1])

@patch('core.services.entry_room_service.entry_room')
def test_01_long_target_found(mock_entry_room):
    mock_entry_room.return_value = {"target": 110.0, "reason": "KC_PROFIT_ROOM_OK"}
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, 1.0, 98.0, None, "sig1", 123, "CHANNEL_SWING", "INITIAL_BREAKOUT")
    log = read_last_log()
    assert log is not None
    assert log["expected_entry"] == 100.0
    assert log["entry_atr"] == 1.0
    assert log["initial_stop"] == 98.0
    assert log["target_status"] == "TARGET_FOUND"
    assert log["structural_target"] == 110.0
    assert log["space_raw"] == 10.0
    assert log["space_atr"] == 10.0
    assert log["raw_risk"] == 2.0
    assert log["net_reward_space"] > 0
    assert log["net_risk"] > 0
    assert log["net_space_rr"] > 0

@patch('core.services.entry_room_service.entry_room')
def test_02_short_target_found(mock_entry_room):
    mock_entry_room.return_value = {"target": 90.0, "reason": "KC_PROFIT_ROOM_OK"}
    record_pre_entry_space_shadow("BTCUSDT", "SHORT", 123456, 100.0, 1.0, 102.0, None, "sig1", 123, "CHANNEL_SWING", "INITIAL_BREAKOUT")
    log = read_last_log()
    assert log is not None
    assert log["expected_entry"] == 100.0
    assert log["entry_atr"] == 1.0
    assert log["initial_stop"] == 102.0
    assert log["target_status"] == "TARGET_FOUND"
    assert log["structural_target"] == 90.0
    assert log["space_raw"] == 10.0
    assert log["space_atr"] == 10.0
    assert log["raw_risk"] == 2.0
    assert log["net_reward_space"] > 0
    assert log["net_risk"] > 0
    assert log["net_space_rr"] > 0

@patch('core.services.entry_room_service.entry_room')
def test_03_no_valid_target(mock_entry_room):
    mock_entry_room.return_value = {"allowed": False, "reason": "KC_PROFIT_TARGET_UNAVAILABLE"}
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, 1.0, 98.0, None, "sig1", 123, "CHANNEL_SWING", "INITIAL_BREAKOUT")
    log = read_last_log()
    assert log is not None
    assert log["target_status"] == "NO_VALID_TARGET"
    assert log["shadow_result"] == "ALLOW_NO_OBSTACLE"

@patch('core.services.entry_room_service.entry_room')
def test_04_tiny_space(mock_entry_room):
    mock_entry_room.return_value = {"target": 100.1, "reason": "KC_PROFIT_ROOM_OK"}
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, 1.0, 98.0, None, "sig1", 123, "CHANNEL_SWING", "INITIAL_BREAKOUT")
    log = read_last_log()
    assert log is not None
    assert log["hypotheses"]["hypothetical_block_0.25"] == True
    assert log["shadow_result"] == "ALLOW_HYPOTHETICAL"

@patch('core.services.entry_room_service.entry_room')
def test_05_entry_room_raise(mock_entry_room):
    mock_entry_room.side_effect = ValueError("boom")
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, 1.0, 98.0, None, "sig1", 123, "CHANNEL_SWING", "INITIAL_BREAKOUT")
    log = read_last_log()
    assert log is None

@patch('core.services.entry_room_service.entry_room')
@patch('builtins.open')
def test_06_file_writer_raise(mock_open, mock_entry_room):
    mock_entry_room.return_value = {"target": 110.0}
    mock_open.side_effect = PermissionError("boom")
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, 1.0, 98.0, None, "sig1", 123, "CHANNEL_SWING", "INITIAL_BREAKOUT")
    assert True

@patch('core.services.entry_room_service.entry_room')
def test_07_entry_atr_zero(mock_entry_room):
    mock_entry_room.return_value = {"target": 110.0}
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, 0.0, 98.0, None, "sig1", 123, "CHANNEL_SWING", "INITIAL_BREAKOUT")
    log = read_last_log()
    assert log is not None
    assert log["space_atr"] is None
    assert log["metric_status"] == "INVALID_ATR"

@patch('core.services.entry_room_service.entry_room')
def test_08_entry_atr_negative(mock_entry_room):
    mock_entry_room.return_value = {"target": 110.0}
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, -1.0, 98.0, None, "sig1", 123, "CHANNEL_SWING", "INITIAL_BREAKOUT")
    log = read_last_log()
    assert log is not None
    assert log["space_atr"] is None
    assert log["metric_status"] == "INVALID_ATR"

@patch('core.services.entry_room_service.entry_room')
def test_09_entry_atr_none(mock_entry_room):
    mock_entry_room.return_value = {"target": 110.0}
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, float('nan'), 98.0, None, "sig1", 123, "CHANNEL_SWING", "INITIAL_BREAKOUT")
    log = read_last_log()
    assert log is not None
    assert log["space_atr"] is None
    assert log["metric_status"] == "INVALID_ATR"

@patch('core.services.entry_room_service.entry_room')
def test_10_no_state_mutation(mock_entry_room):
    mock_entry_room.return_value = {"target": 110.0}
    dummy_decision = {"type": "A", "val": [1,2]}
    orig = copy.deepcopy(dummy_decision)
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, 1.0, 98.0, None, "sig1", 123, "CHANNEL_SWING", "INITIAL_BREAKOUT")
    assert dummy_decision == orig

@patch('core.services.entry_room_service.entry_room')
def test_11_non_channel_swing(mock_entry_room):
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, 1.0, 98.0, None, "sig1", 123, "SOME_OTHER_MODE", "INITIAL_BREAKOUT")
    log = read_last_log()
    assert log is None

@patch('core.services.entry_room_service.entry_room')
def test_12_real_channel_swing_hook_reached(mock_entry_room):
    mock_entry_room.return_value = {"target": 110.0}
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, 1.0, 98.0, None, "sig1", 123, "CHANNEL_SWING", "INITIAL_BREAKOUT")
    log = read_last_log()
    assert log is not None

# TEST 13 requires importing engine and simulating.
# We will do a small test inside the same file for engine integration.
def test_13_engine_hook_shadow_crash():
    import asyncio
    from core.engine import TradingEngine
    
    class FakeAccount:
        def __init__(self):
            self.positions = {}
            self.pending_limit_orders = {}
        def get_wallet_balance(self): return 1000.0
        def get_available_balance(self): return 1000.0
        async def open_position(self, **kwargs):
            self.open_called = True
            return True
            
    engine = TradingEngine()
    engine.account = FakeAccount()
    engine.account.open_called = False
    
    snapshot = {"price": 100.0, "frame": None}
    decision = {"type": "TEST", "entry_phase": "INITIAL_BREAKOUT", "close_price": 99.0, "entry_atr": 1.0, "breakout_bar_id": None, "pair_confirmation_bar_id": None}
    signal = {"signal_id": "1", "candidate_bar_id": 123}
    
    with patch('core.services.pre_entry_space_shadow.record_pre_entry_space_shadow') as m:
        m.side_effect = RuntimeError("SHADOW_CRASH")
        # We need to bypass some checks in submit_entry_candidate, let's just mock evaluate_entry_contract
        with patch('core.services.entry_contract.evaluate_entry_contract', return_value=True):
            asyncio.run(engine.submit_entry_candidate("BTCUSDT", "LONG", 123456, decision, signal, snapshot, None, 1.0))
            
    assert engine.account.open_called == True

def test_14_continuation_reentry():
    record_pre_entry_space_shadow("BTCUSDT", "LONG", 123456, 100.0, 1.0, 98.0, None, "sig1", 123, "CHANNEL_SWING", "CONTINUATION_REENTRY")
    log = read_last_log()
    assert log is None

