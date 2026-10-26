import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from core.testnet_account import BinanceTestnetAccount

def setup_mock_account(tmp_path):
    exchange = MagicMock()
    exchange.price_to_precision = MagicMock(return_value="0.10")
    account = BinanceTestnetAccount(
        exchange=exchange, state_file=str(tmp_path / "testnet.json"),
    )
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
        "open_timestamp": 1_700_000_000.0,
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
async def test_profit_giveback_cannot_bypass_trend_lock(tmp_path):
    account = setup_mock_account(tmp_path)
    # Trigger Early Profit Guard (curr_unrealized < max * 0.75)
    pos = create_mock_position('LONG', curr_unrealized=10.0, max_unrealized=20.0, entry_price=0.10, trend_status='RELEASED')
    account.positions = {"龙虾/USDT": pos}
    account.position_meta = {"龙虾/USDT": create_mock_meta(20.0)}
    
    # Track status so that it doesn't try to close multiple times
    async def mock_close(*args, **kwargs):
        pos['status'] = "CLOSED"
        
    account.close_position.side_effect = mock_close
    
    await account.update_positions({"龙虾/USDT": 0.10})
    
    # This legacy peak giveback is a strategy exit and needs closed-bar Trend
    # Lock evidence. No frame is supplied, so the account boundary must not
    # receive an ordinary close request.
    assert account.close_position.call_count == 0

@pytest.mark.anyio
async def test_legacy_profit_giveback_with_competing_exit_cannot_bypass_trend_lock(
    tmp_path,
):
    account = setup_mock_account(tmp_path)
    # Trigger Early Profit Guard but also trigger some other close logic (if any)
    # However since Extreme Profit Giveback Protection has `continue` right after, it guarantees single close!
    pos = create_mock_position('LONG', curr_unrealized=10.0, max_unrealized=20.0, entry_price=0.10, trend_status='RELEASED')
    account.positions = {"龙虾/USDT": pos}
    account.position_meta = {"龙虾/USDT": create_mock_meta(20.0)}
    
    async def mock_close(*args, **kwargs):
        pos['status'] = "CLOSED"
        
    account.close_position.side_effect = mock_close
    
    await account.update_positions({"龙虾/USDT": 0.10})
    
    # Multiple ordinary profit heuristics still cannot authorize the same-bar
    # close when there is no closed-bar Trend Lock evidence.
    assert account.close_position.call_count == 0
