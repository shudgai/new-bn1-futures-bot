import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from core.paper_account import PaperAccount

    

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
    
