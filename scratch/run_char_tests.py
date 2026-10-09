import asyncio
from unittest.mock import AsyncMock, MagicMock
from core.testnet_account import BinanceTestnetAccount

async def run_tests():
    acc = BinanceTestnetAccount(MagicMock())
    acc.exchange = MagicMock()
    acc.exchange.price_to_precision = lambda sym, p: f"{p:.4f}"
    acc._cancel_all_orders = AsyncMock()
    acc._create_protection_order = AsyncMock(return_value={"algoId": "123"})
    acc.save_state = MagicMock()
    acc.log = MagicMock()
    
    # Test 1
    symbol = "TEST/USDT"
    acc.positions[symbol] = {"side": "LONG", "qty": 1.0}
    acc.position_meta[symbol] = {"entry_mode": "CHANNEL_SWING", "sl": 100.0}
    
    success = await acc.trail_stop_loss(symbol, 110.0, mark_profit_locked=True)
    assert success is True
    acc._create_protection_order.assert_not_called()
    acc._cancel_all_orders.assert_not_called()
    assert acc.positions[symbol]["sl"] == 110.0
    print("Test 1: CHANNEL_SWING bypass bug confirmed")
    
    # Test 2
    acc._cancel_all_orders.reset_mock()
    acc._create_protection_order.reset_mock()
    
    acc.position_meta[symbol] = {"entry_mode": "MANUAL", "sl": 100.0}
    acc._create_protection_order.side_effect = Exception("API Error")
    
    try:
        await acc.trail_stop_loss(symbol, 120.0, mark_profit_locked=True)
    except Exception:
        pass
    
    acc._cancel_all_orders.assert_called_once()
    print("Test 2: Normal path naked position bug confirmed")

asyncio.run(run_tests())
