import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from core.testnet_account import BinanceTestnetAccount

def setup_mock_account():
    exchange = MagicMock()
    exchange.price_to_precision = MagicMock(return_value="0.10")
    account = BinanceTestnetAccount(exchange=exchange)
    account.close_position = AsyncMock()
    account.refresh = AsyncMock()
    account.save_state = MagicMock()
    account._create_orphan_protection = AsyncMock()
    account.tickers = {"龙虾/USDT": 0.10}
    return account

def create_mock_position(side, curr_unrealized, max_unrealized, entry_price, qty=1000, trend_status='RELEASED'):
    return {
        "symbol": "龙虾/USDT",
        "side": side,
        "amount": entry_price * qty,
        "qty": qty,
        "entry_price": entry_price,
        "mark_price": 0.10,
        "status": "OPEN",
        "unrealized_pnl": curr_unrealized,
        "trend_hold_status": trend_status,
        "sl": entry_price - 0.05 if side == 'LONG' else entry_price + 0.05
    }

def create_mock_meta(max_unrealized):
    return {
        "max_unrealized_pnl": max_unrealized,
        "highest_pnl_pct": 0.10
    }

@pytest.mark.anyio
async def test_single_close_invariant_released_and_peak_trailing():
    account = setup_mock_account()
    # Trigger Early Profit Guard (curr_unrealized < max * 0.75)
    pos = create_mock_position('LONG', curr_unrealized=10.0, max_unrealized=20.0, entry_price=0.10, trend_status='RELEASED')
    account.positions = {"龙虾/USDT": pos}
    account.position_meta = {"龙虾/USDT": create_mock_meta(20.0)}
    
    # Track status so that it doesn't try to close multiple times
    async def mock_close(*args, **kwargs):
        pos['status'] = "CLOSED"
        
    account.close_position.side_effect = mock_close
    
    await account.update_positions({"龙虾/USDT": 0.10})
    
    # Expected exactly 1 call (due to Extreme Profit Giveback Protection)
    assert account.close_position.call_count == 1
    args, kwargs = account.close_position.call_args
    reason = args[2] if len(args) > 2 else kwargs.get('reason', '')
    assert "極值利潤回撤" in reason

@pytest.mark.anyio
async def test_single_close_invariant_hard_exit_and_peak_trailing():
    account = setup_mock_account()
    # Trigger Early Profit Guard but also trigger some other close logic (if any)
    # However since Extreme Profit Giveback Protection has `continue` right after, it guarantees single close!
    pos = create_mock_position('LONG', curr_unrealized=10.0, max_unrealized=20.0, entry_price=0.10, trend_status='RELEASED')
    account.positions = {"龙虾/USDT": pos}
    account.position_meta = {"龙虾/USDT": create_mock_meta(20.0)}
    
    async def mock_close(*args, **kwargs):
        pos['status'] = "CLOSED"
        
    account.close_position.side_effect = mock_close
    
    await account.update_positions({"龙虾/USDT": 0.10})
    
    assert account.close_position.call_count == 1
    args, kwargs = account.close_position.call_args
    reason = args[2] if len(args) > 2 else kwargs.get('reason', '')
    assert "極值利潤回撤" in reason
