import pytest
import pandas as pd
import time
import asyncio
from unittest.mock import Mock, MagicMock
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.engine import TradingEngine

class MockAccount:
    def __init__(self):
        self.positions = {}
        self.position_meta = {}
    def log(self, *args, **kwargs):
        pass

class MockEngine:
    def __init__(self):
        self.account = MockAccount()
        self._market_crash_entry_cooldown_until = 0.0

    def _market_crash_entries_paused(self, now=None):
        if now is None:
            now = time.time()
        return self._market_crash_entry_cooldown_until > now

    _execute_confirmed_channel_break = TradingEngine._execute_confirmed_channel_break

class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super(AsyncMock, self).__call__(*args, **kwargs)

def create_mock_frame(timestamps):
    df = pd.DataFrame({
        'timestamp': timestamps,
        'open': [10]*len(timestamps),
        'high': [10]*len(timestamps),
        'low': [10]*len(timestamps),
        'close': [10]*len(timestamps),
        'is_closed': [True]*len(timestamps)
    })
    return df

@pytest.fixture
def engine_mocked():
    e = MockEngine()
    e._place_structured_entry = AsyncMock(return_value=True)
    return e

def test_1_normal_entry_allowed(engine_mocked):
    frame = create_mock_frame([1000, 2000, 3000])
    res = asyncio.run(engine_mocked._execute_confirmed_channel_break("TEST", frame, 10, "LONG"))
    assert res is True

def test_2_active_crash_blocked(engine_mocked):
    engine_mocked._market_crash_entry_cooldown_until = time.time() + 1000
    frame = create_mock_frame([1000, 2000, 3000])
    res = asyncio.run(engine_mocked._execute_confirmed_channel_break("TEST", frame, 10, "LONG"))
    assert res is False

def test_3_stale_setup_blocked(engine_mocked):
    now = time.time()
    engine_mocked._market_crash_entry_cooldown_until = now - 5
    conf_ts = (now - 10) * 1000
    frame = create_mock_frame([conf_ts - 120000, conf_ts - 60000, conf_ts])
    res = asyncio.run(engine_mocked._execute_confirmed_channel_break("TEST", frame, 10, "LONG"))
    assert res is False

def test_4_new_setup_allowed(engine_mocked):
    now = time.time()
    engine_mocked._market_crash_entry_cooldown_until = now - 5
    conf_ts = (now - 2) * 1000
    frame = create_mock_frame([conf_ts - 120000, conf_ts - 60000, conf_ts])
    res = asyncio.run(engine_mocked._execute_confirmed_channel_break("TEST", frame, 10, "LONG"))
    assert res is True

def test_5_daily_halt_blocks(engine_mocked):
    frame = create_mock_frame([1000])
    res = asyncio.run(engine_mocked._execute_confirmed_channel_break("TEST", frame, 10, "LONG", daily_halt=True))
    assert res is False

def test_6_existing_pos_blocks(engine_mocked):
    engine_mocked.account.positions["TEST"] = {}
    frame = create_mock_frame([1000])
    res = asyncio.run(engine_mocked._execute_confirmed_channel_break("TEST", frame, 10, "LONG"))
    assert res is False

def test_7_empty_frame_blocks(engine_mocked):
    res = asyncio.run(engine_mocked._execute_confirmed_channel_break("TEST", pd.DataFrame(), 10, "LONG"))
    assert res is False

def test_8_none_frame_blocks(engine_mocked):
    res = asyncio.run(engine_mocked._execute_confirmed_channel_break("TEST", None, 10, "LONG"))
    assert res is False

def test_9_stale_setup_exact_boundary(engine_mocked):
    now = time.time()
    engine_mocked._market_crash_entry_cooldown_until = now - 5
    conf_ts = (now - 5) * 1000
    frame = create_mock_frame([conf_ts - 120000, conf_ts - 60000, conf_ts])
    res = asyncio.run(engine_mocked._execute_confirmed_channel_break("TEST", frame, 10, "LONG"))
    assert res is False

def test_10_stale_setup_post_boundary(engine_mocked):
    now = time.time()
    engine_mocked._market_crash_entry_cooldown_until = now - 5
    conf_ts = (now - 4.9) * 1000
    frame = create_mock_frame([conf_ts - 120000, conf_ts - 60000, conf_ts])
    res = asyncio.run(engine_mocked._execute_confirmed_channel_break("TEST", frame, 10, "LONG"))
    assert res is True
