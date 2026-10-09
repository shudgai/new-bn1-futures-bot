"""HARD SAFETY: BinanceTestnetAccount state path isolation proofs."""
import os
from unittest.mock import MagicMock

import pytest

import core.testnet_account as ta
from core.testnet_account import BinanceTestnetAccount
from tests.conftest import PRODUCTION_TESTNET_STATE_FILE


def test_production_default_path_unchanged(monkeypatch):
    """No state_file => module STATE_FILE == <repo>/data/testnet_account.json."""
    monkeypatch.undo()
    original = BinanceTestnetAccount.__dict__["_state_path"]
    bare = BinanceTestnetAccount.__new__(BinanceTestnetAccount)  # no I/O
    bare._state_file = None
    assert original(bare) == ta.STATE_FILE
    assert os.path.realpath(ta.STATE_FILE) == os.path.realpath(PRODUCTION_TESTNET_STATE_FILE)
    legacy = BinanceTestnetAccount.__new__(BinanceTestnetAccount)  # attr absent
    assert original(legacy) == ta.STATE_FILE


def test_guard_blocks_default_path_in_tests():
    with pytest.raises(RuntimeError, match="TEST_ISOLATION"):
        BinanceTestnetAccount(MagicMock())


def test_injected_path_round_trip(testnet_state_file):
    acct = BinanceTestnetAccount(MagicMock(), state_file=testnet_state_file)
    assert acct._state_path() == testnet_state_file
    assert acct.staged_state_directory == f"{testnet_state_file}.staged"
    acct.circuit_breaker_latched = True
    acct.live_session_start_equity = "10000"
    acct.save_state(strict=True)
    assert os.path.exists(testnet_state_file)
    reloaded = BinanceTestnetAccount(MagicMock(), state_file=testnet_state_file)
    assert reloaded.circuit_breaker_latched is True
    assert reloaded.live_session_start_equity == "10000"
