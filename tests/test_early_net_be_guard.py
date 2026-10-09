import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from core.paper_account import PaperAccount

def test_early_net_be_long_ceiling():
    account = PaperAccount()
    account.positions = {
        "TEST": {
            "side": "LONG",
            "qty": 1.0,
            "entry_price": 100.0,
            "sl": 90.0,
            "open_timestamp": 1234567890.0,
        }
    }
    account.position_meta = {"TEST": {}}
    account.trail_stop_loss = AsyncMock(return_value=True)
    
    # Cost = 100 * 1 * 0.0005 * 2 + 100 * 1 * 0.0005 = 0.1 + 0.05 = 0.15
    # candidate net-be raw = 100.15
    # with math.ceil(100.15 / 0.0001 - 1e-9) * 0.0001 -> 100.1500
    
    asyncio.run(account.update_positions({"TEST": 100.2})) # unrealized = (100.2 - 100.0) = 0.20 > 0.15
    
    assert account.trail_stop_loss.called
    args, kwargs = account.trail_stop_loss.call_args
    assert args[0] == "TEST"
    assert args[1] >= 100.15
    assert kwargs.get("mark_profit_locked") == True
    
    pos = account.positions["TEST"]
    assert pos.get("early_net_be_protected") == True
    
def test_early_net_be_short_floor():
    account = PaperAccount()
    account.positions = {
        "TEST": {
            "side": "SHORT",
            "qty": 1.0,
            "entry_price": 100.0,
            "sl": 110.0,
            "open_timestamp": 1234567890.0,
        }
    }
    account.position_meta = {"TEST": {}}
    account.trail_stop_loss = AsyncMock(return_value=True)
    
    asyncio.run(account.update_positions({"TEST": 99.8})) # unrealized 0.2 > 0.15
    
    assert account.trail_stop_loss.called
    args, kwargs = account.trail_stop_loss.call_args
    assert args[1] <= 99.85
    pos = account.positions["TEST"]
    assert pos.get("early_net_be_protected") == True

def test_failed_stop_update_preserves_old_sl():
    account = PaperAccount()
    account.positions = {
        "TEST": {
            "side": "LONG",
            "qty": 1.0,
            "entry_price": 100.0,
            "sl": 90.0,
            "open_timestamp": 1234567890.0,
        }
    }
    account.position_meta = {"TEST": {}}
    # Mock failure
    account.trail_stop_loss = AsyncMock(return_value=False)
    
    asyncio.run(account.update_positions({"TEST": 100.2}))
    
    pos = account.positions["TEST"]
    assert pos.get("early_net_be_protected", False) == False
    
def test_already_better_sl_marks_protected_without_update():
    account = PaperAccount()
    account.positions = {
        "TEST": {
            "side": "LONG",
            "qty": 1.0,
            "entry_price": 100.0,
            "sl": 101.0, # 101.0 is already better than Net-BE candidate (100.15)
            "open_timestamp": 1234567890.0,
        }
    }
    account.position_meta = {"TEST": {}}
    account.trail_stop_loss = AsyncMock(return_value=True)
    
    asyncio.run(account.update_positions({"TEST": 102.0}))
    
    assert not account.trail_stop_loss.called
    pos = account.positions["TEST"]
    assert pos.get("early_net_be_protected") == True
