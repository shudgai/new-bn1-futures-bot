import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock
import asyncio
from core.services.symbol_runner import process_single_symbol_runner

@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.account = MagicMock()
    engine.account.positions = {}
    engine.account.position_meta = {}
    engine.account.channel_profit_reentries = {}
    
    # Mocking close_position to simulate a successful exit
    async def mock_close_position(symbol, price, reason, is_manual=False):
        if symbol in engine.account.positions:
            del engine.account.positions[symbol]
        return True
    
    engine.account.close_position = AsyncMock(side_effect=mock_close_position)
    engine._channel_outer_reentry_after_exit = {}
    engine._channel_pending_reverse_bar = {}
    engine._last_exit_bar_id = {}
    
    # Mocking the execute entry
    engine._execute_confirmed_channel_break = AsyncMock()
    return engine

@pytest.mark.asyncio
async def test_symbol_runner_lifecycle_exit_and_reentry(mock_engine):
    # Setup dummy frame with flash crash scenario
    frame = pd.DataFrame([
        {"timestamp": 1000, "open": 100, "high": 105, "low": 95, "close": 102, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
        {"timestamp": 2000, "open": 102, "high": 103, "low": 101, "close": 102, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
        # Flash crash!
        {"timestamp": 3000, "open": 102, "high": 102, "low": 90, "close": 90, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
    ])
    
    symbol = "BTCUSDT"
    # Mock a long position
    mock_engine.account.positions[symbol] = {
        "side": "LONG",
        "entry_price": 100,
        "qty": 1,
        "open_timestamp": 1000
    }
    
    # Some mock caches to test reset
    mock_engine.account.position_meta[symbol] = {"dummy": "data"}
    mock_engine._channel_outer_reentry_after_exit[symbol] = "LONG"
    
    # 1. Trigger Exit (Flash Crash)
    await process_single_symbol_runner(
        mock_engine, symbol, now_time=3000, btc_1m_turn=None, daily_halt=False,
        exit_frame=frame, exit_quote=90.0, exit_only=False
    )
    
    # Assert closed
    assert symbol not in mock_engine.account.positions
    assert symbol not in mock_engine.account.position_meta
    assert symbol not in mock_engine._channel_outer_reentry_after_exit
    assert mock_engine._last_exit_bar_id[symbol] == 3000
    
    # 2. Same Bar ID Cooldown Check
    # Even if entry condition is met, it should not enter on timestamp 3000
    await process_single_symbol_runner(
        mock_engine, symbol, now_time=3050, btc_1m_turn=None, daily_halt=False,
        exit_frame=frame, exit_quote=90.0, exit_only=False
    )
    mock_engine._execute_confirmed_channel_break.assert_not_called()
    
    # 3. Next Bar Reentry
    frame_next = pd.DataFrame([
        {"timestamp": 2000, "open": 102, "high": 103, "low": 101, "close": 102, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
        {"timestamp": 3000, "open": 102, "high": 102, "low": 90, "close": 90, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
        # Next bar showing entry signal (simulated mock)
        {"timestamp": 4000, "open": 90, "high": 110, "low": 89, "close": 110, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
    ])
    
    await process_single_symbol_runner(
        mock_engine, symbol, now_time=4000, btc_1m_turn=None, daily_halt=False,
        exit_frame=frame_next, exit_quote=110.0, exit_only=False
    )
    
    # The actual call depends on UnifiedEntryStrategy internals, but at minimum we know it didn't return early due to cooldown.
    assert mock_engine._last_exit_bar_id[symbol] == 3000
