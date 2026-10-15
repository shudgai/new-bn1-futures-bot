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


