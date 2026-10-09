import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.fixture
def testnet_account(testnet_state_file):
    from core.testnet_account import BinanceTestnetAccount
    mock_exchange = MagicMock()
    mock_exchange.price_to_precision = lambda sym, val: f"{val:.6f}"
    mock_exchange.amount_to_precision = lambda sym, val: f"{val:.4f}"
    
    mock_exchange.cancel_all_orders = AsyncMock(return_value=True)
    mock_exchange.fetch_open_orders = AsyncMock(return_value=[{"id": "old1", "type": "stop_market"}])
    mock_exchange.request = AsyncMock(return_value={"id": "algo123", "algoId": "algo123"})
    
    account = BinanceTestnetAccount(mock_exchange, state_file=testnet_state_file)
    account.positions = {
        "DOGE/USDT": {
            "symbol": "DOGE/USDT",
            "side": "LONG",
            "qty": 1000,
            "entry_mode": "CHANNEL_SWING",
            "sl": 0.1,
            "is_breakeven_moved": False
        }
    }
    account.position_meta = {
        "DOGE/USDT": {
            "entry_mode": "CHANNEL_SWING",
            "sl": 0.1,
            "is_breakeven_moved": False
        }
    }
    account.closing_lock = set()
    account.save_state = MagicMock()
    return account

@pytest.mark.anyio
async def test_1_channel_swing_mark_profit_locked_calls_exchange(testnet_account):
    """TEST 1 & TEST 2 & TEST 6: Channel Swing + mark_profit_locked + LONG MUST call exchange"""
    res = await testnet_account.trail_stop_loss("DOGE/USDT", 0.15, mark_profit_locked=True)
    
    assert res is True
    # Verify local state is updated
    assert testnet_account.position_meta["DOGE/USDT"]["sl"] == 0.15
    assert testnet_account.positions["DOGE/USDT"]["sl"] == 0.15
    assert testnet_account.position_meta["DOGE/USDT"]["is_breakeven_moved"] is True
    assert testnet_account.positions["DOGE/USDT"]["is_breakeven_moved"] is True
    
    # Verify exchange was called
    testnet_account.exchange.request.assert_called()
    assert testnet_account.save_state.call_count >= 1

@pytest.mark.anyio
async def test_3_create_protection_failure_does_not_return_true(testnet_account):
    """TEST 3 & TEST 4: Create protection failure must NOT falsely return True"""
    testnet_account.exchange.request = AsyncMock(side_effect=Exception("Exchange error"))
    
    res = await testnet_account.trail_stop_loss("DOGE/USDT", 0.15, mark_profit_locked=True)
    
    assert res is False
    # Verify local state is NOT updated
    assert testnet_account.position_meta["DOGE/USDT"]["sl"] == 0.1
    assert testnet_account.positions["DOGE/USDT"]["sl"] == 0.1
    assert testnet_account.position_meta["DOGE/USDT"]["is_breakeven_moved"] is False
    assert testnet_account.positions["DOGE/USDT"]["is_breakeven_moved"] is False

@pytest.mark.anyio
async def test_5_normal_non_channel_swing_path(testnet_account):
    """TEST 5: Normal non-Channel-Swing path"""
    testnet_account.positions["DOGE/USDT"]["entry_mode"] = "OTHER"
    testnet_account.position_meta["DOGE/USDT"]["entry_mode"] = "OTHER"
    
    res = await testnet_account.trail_stop_loss("DOGE/USDT", 0.15, mark_profit_locked=False)
    
    assert res is True
    assert testnet_account.position_meta["DOGE/USDT"]["sl"] == 0.15
    testnet_account.exchange.request.assert_called()

@pytest.mark.anyio
async def test_7_short_direction_channel_swing(testnet_account):
    """TEST 7: SHORT direction"""
    testnet_account.positions["DOGE/USDT"]["side"] = "SHORT"
    testnet_account.positions["DOGE/USDT"]["sl"] = 0.2
    testnet_account.position_meta["DOGE/USDT"]["sl"] = 0.2
    
    res = await testnet_account.trail_stop_loss("DOGE/USDT", 0.15, mark_profit_locked=True)
    
    assert res is True
    assert testnet_account.position_meta["DOGE/USDT"]["sl"] == 0.15
    testnet_account.exchange.request.assert_called()
