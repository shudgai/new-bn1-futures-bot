import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from core.testnet_account import BinanceTestnetAccount

@pytest.fixture
def account():
    acc = BinanceTestnetAccount(MagicMock())
    acc.exchange = MagicMock()
    acc.exchange.price_to_precision = lambda sym, p: f"{p:.4f}"
    acc._cancel_all_orders = AsyncMock()
    acc._create_protection_order = AsyncMock(return_value={"algoId": "123"})
    acc.save_state = MagicMock()
    acc.log = MagicMock()
    return acc

@pytest.mark.asyncio
async def test_channel_swing_bypass_bug(account):
    """Test 1: CHANNEL_SWING returns True but creates 0 orders."""
    symbol = "TEST/USDT"
    account.positions[symbol] = {"side": "LONG", "qty": 1.0}
    account.position_meta[symbol] = {"entry_mode": "CHANNEL_SWING", "sl": 100.0}
    
    # New SL is higher
    success = await account.trail_stop_loss(symbol, 110.0, mark_profit_locked=True)
    
    assert success is True
    # The bug: no exchange order was created
    account._create_protection_order.assert_not_called()
    account._cancel_all_orders.assert_not_called()
    # But local state claims it's protected
    assert account.positions[symbol]["sl"] == 110.0

@pytest.mark.asyncio
async def test_normal_path_naked_position_bug(account):
    """Test 2: Normal path cancels old, fails to create new, leaving naked position."""
    symbol = "TEST/USDT"
    account.positions[symbol] = {"side": "LONG", "qty": 1.0}
    account.position_meta[symbol] = {"entry_mode": "MANUAL", "sl": 100.0}
    
    # Simulate API failure during create
    account._create_protection_order.side_effect = Exception("API Error")
    
    with pytest.raises(Exception):
        await account.trail_stop_loss(symbol, 110.0, mark_profit_locked=True)
        
    # The bug: old orders were cancelled
    account._cancel_all_orders.assert_called_once()
    # And because it raised, local SL is NOT updated to 0 or reverted to 100 properly in exchange
    # Exchange is now NAKED.
    assert True
