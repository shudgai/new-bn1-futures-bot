import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock
import asyncio
from core.services.symbol_runner import process_single_symbol_runner

@pytest.fixture
def anyio_backend():
    return "asyncio"


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





@pytest.mark.anyio
async def test_flat_direction_does_not_send_wait_tuple(mock_engine):
    frame = pd.DataFrame([dict(timestamp=i, kc_middle=100) for i in range(4)])
    await process_single_symbol_runner(
        mock_engine, "TEST/USDT", 0, None, False,
        exit_frame=frame, exit_quote=100,
    )
    mock_engine._execute_confirmed_channel_break.assert_not_awaited()
    assert not any("處理失敗" in call.args[0] for call in mock_engine.account.log.call_args_list)
