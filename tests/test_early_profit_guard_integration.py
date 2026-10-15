from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
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

def create_mock_position(side, curr_unrealized, max_unrealized, entry_price, qty=1000, trend_status='WARNING'):
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
    }

def create_mock_meta(max_unrealized):
    return {
        "max_unrealized_pnl": max_unrealized,
        "highest_pnl_pct": 0.10
    }






def test_short_unknown_blocks_early_profit_guard():
    pos = {
        'symbol': '1000LUNCUSDT', 'side': 'SHORT',
        'entry_price': 100.0, 'qty': 1.0,
        'peak_trailing_state': {'peak_net_pnl': 3.5}
    }

    # Simulate missing data (UNKNOWN) -> STALE_SNAPSHOT
    snapshot = {'reason': 'STALE_SNAPSHOT'}
    decision = evaluate_peak_trailing(pos, 98.0, snapshot, 0.0) # Using 0 atr since stale
    assert decision is None

def test_long_unknown_blocks_early_profit_guard():
    pos = {
        'symbol': '1000LUNCUSDT', 'side': 'LONG',
        'entry_price': 100.0, 'qty': 1.0,
        'peak_trailing_state': {'peak_net_pnl': 3.5}
    }

    # Simulate missing data (UNKNOWN)
    snapshot = {'reason': 'NO_DATA'}
    decision = evaluate_peak_trailing(pos, 102.0, snapshot, 0.0)
    assert decision is None
