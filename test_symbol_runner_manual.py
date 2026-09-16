import asyncio
import pandas as pd
from unittest.mock import MagicMock, AsyncMock
from core.services.symbol_runner import process_single_symbol_runner

async def main():
    engine = MagicMock()
    engine.account = MagicMock()
    engine.account.positions = {}
    engine.account.position_meta = {}
    engine.account.channel_profit_reentries = {}
    
    async def mock_close_position(symbol, price, reason, is_manual=False):
        if symbol in engine.account.positions:
            del engine.account.positions[symbol]
        return True
    
    engine.account.close_position = AsyncMock(side_effect=mock_close_position)
    engine._channel_outer_reentry_after_exit = {}
    engine._channel_pending_reverse_bar = {}
    engine._last_exit_bar_id = {}
    engine._execute_confirmed_channel_break = AsyncMock()
    
    frame = pd.DataFrame([
        {"timestamp": 1000, "open": 100, "high": 105, "low": 95, "close": 102, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
        {"timestamp": 2000, "open": 102, "high": 103, "low": 101, "close": 102, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
        {"timestamp": 3000, "open": 102, "high": 102, "low": 90, "close": 90, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
    ])
    
    symbol = "BTCUSDT"
    engine.account.positions[symbol] = {
        "side": "LONG",
        "entry_price": 100,
        "qty": 1,
        "open_timestamp": 1000
    }
    engine.account.position_meta[symbol] = {"dummy": "data"}
    engine._channel_outer_reentry_after_exit[symbol] = "LONG"
    
    await process_single_symbol_runner(
        engine, symbol, now_time=3000, btc_1m_turn=None, daily_halt=False,
        exit_frame=frame, exit_quote=90.0, exit_only=False
    )
    
    assert symbol not in engine.account.positions, "Position not cleared"
    assert symbol not in engine.account.position_meta, "Meta not cleared"
    assert symbol not in engine._channel_outer_reentry_after_exit, "Reentry cache not cleared"
    assert engine._last_exit_bar_id.get(symbol) == 3000, "Exit bar ID not recorded"
    print("Test 1: Reset successful")
    
    await process_single_symbol_runner(
        engine, symbol, now_time=3050, btc_1m_turn=None, daily_halt=False,
        exit_frame=frame, exit_quote=90.0, exit_only=False
    )
    engine._execute_confirmed_channel_break.assert_not_called()
    print("Test 2: Cooldown successful")
    
    frame_next = pd.DataFrame([
        {"timestamp": 2000, "open": 102, "high": 103, "low": 101, "close": 102, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
        {"timestamp": 3000, "open": 102, "high": 102, "low": 90, "close": 90, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
        {"timestamp": 4000, "open": 90, "high": 110, "low": 89, "close": 110, "kc_upper": 105, "kc_lower": 95, "kc_middle": 100, "atr": 2},
    ])
    
    await process_single_symbol_runner(
        engine, symbol, now_time=4000, btc_1m_turn=None, daily_halt=False,
        exit_frame=frame_next, exit_quote=110.0, exit_only=False
    )
    assert engine._last_exit_bar_id[symbol] == 3000, "Exit bar ID should remain unchanged"
    print("Test 3: Re-entry bypass successful")

if __name__ == "__main__":
    asyncio.run(main())
