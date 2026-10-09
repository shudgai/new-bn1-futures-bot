import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from core.testnet_account import BinanceTestnetAccount

@pytest.fixture
def mock_account(testnet_state_file):
    account = BinanceTestnetAccount(AsyncMock(), state_file=testnet_state_file)
    account.exchange = AsyncMock()
    account.exchange.fetch_open_orders = AsyncMock(return_value=[])
    account.exchange.request = AsyncMock(return_value=[])
    account._raw_symbol = lambda s: s.replace("/", "")
    account.positions = {}
    account.pending_limit_orders = {}
    account.position_meta = {}
    return account


def test_position_with_one_valid_sl(mock_account):
    # position + one valid SL -> POSITION_PROTECTED_OBSERVED=TRUE
    mock_account.positions["BTC/USDT"] = {"side": "LONG", "qty": 1.0}
    mock_account.position_meta["BTC/USDT"] = {"sl": 90000.0}
    
    mock_account.exchange.request.return_value = [
        {"algoId": "1", "symbol": "BTCUSDT", "type": "STOP_MARKET", "algoStatus": "WORKING", "triggerPrice": "90000.0", "side": "SELL", "reduceOnly": True}
    ]
    
    asyncio.run(mock_account._fetch_exchange_order_snapshot())
    assert mock_account._reconciliation_status == "SUCCESS"
    assert "BTC/USDT" in mock_account.positions
    # Ensure zero auto-repair calls
    assert mock_account.exchange.create_order.call_count == 0
    assert mock_account.exchange.cancel_order.call_count == 0
    assert mock_account.exchange.cancel_all_orders.call_count == 0


def test_position_with_no_sl(mock_account):
    # position + no SL
    mock_account.positions["BTC/USDT"] = {"side": "LONG", "qty": 1.0}
    mock_account.exchange.request.return_value = []
    
    asyncio.run(mock_account._fetch_exchange_order_snapshot())
    assert mock_account._reconciliation_status == "SUCCESS"
    assert mock_account.exchange.create_order.call_count == 0


def test_position_with_two_sl(mock_account):
    # position + two SL -> MULTIPLE_EXCHANGE_SL
    mock_account.positions["BTC/USDT"] = {"side": "LONG", "qty": 1.0}
    mock_account.exchange.request.return_value = [
        {"algoId": "1", "symbol": "BTCUSDT", "type": "STOP_MARKET", "algoStatus": "WORKING", "triggerPrice": "90000.0"},
        {"algoId": "2", "symbol": "BTCUSDT", "type": "STOP_MARKET", "algoStatus": "WORKING", "triggerPrice": "91000.0"}
    ]
    
    asyncio.run(mock_account._fetch_exchange_order_snapshot())
    assert mock_account._reconciliation_status == "SUCCESS"
    # zero cancel calls
    assert mock_account.exchange.cancel_order.call_count == 0
    delete_calls = [c for c in mock_account.exchange.request.call_args_list if c[0][2] == "DELETE"]
    assert len(delete_calls) == 0


def test_exchange_pending_entry_local_missing(mock_account):
    mock_account.exchange.fetch_open_orders.return_value = [
        {"id": "entry1", "symbol": "BTC/USDT", "type": "limit", "info": {"reduceOnly": False}}
    ]
    mock_account.positions["BTC/USDT"] = {} # Just to trigger check
    
    asyncio.run(mock_account._fetch_exchange_order_snapshot())
    assert mock_account._reconciliation_status == "SUCCESS"
    assert "BTC/USDT" not in mock_account.pending_limit_orders


def test_local_pending_exchange_missing(mock_account):
    mock_account.pending_limit_orders["BTC/USDT"] = {"order_id": "1"}
    mock_account.exchange.fetch_open_orders.return_value = []
    
    asyncio.run(mock_account._fetch_exchange_order_snapshot())
    assert mock_account._reconciliation_status == "SUCCESS"
    assert "BTC/USDT" in mock_account.pending_limit_orders


def test_orphan_reduce_only(mock_account):
    mock_account.position_meta["BTC/USDT"] = {"sl": 90000.0} # local meta but no position
    mock_account.exchange.request.return_value = [
        {"algoId": "1", "symbol": "BTCUSDT", "type": "STOP_MARKET", "algoStatus": "WORKING"}
    ]
    
    asyncio.run(mock_account._fetch_exchange_order_snapshot())
    assert mock_account._reconciliation_status == "SUCCESS"
    delete_calls = [c for c in mock_account.exchange.request.call_args_list if c[0][2] == "DELETE"]
    assert len(delete_calls) == 0


def test_algo_api_timeout(mock_account):
    mock_account.positions["BTC/USDT"] = {"side": "LONG", "qty": 1.0}
    mock_account.exchange.request.side_effect = Exception("Timeout")
    
    asyncio.run(mock_account._fetch_exchange_order_snapshot())
    assert mock_account._reconciliation_status == "ERROR"


def test_malformed_response(mock_account):
    mock_account.positions["BTC/USDT"] = {"side": "LONG", "qty": 1.0}
    mock_account.exchange.request.return_value = [{"bad_key": "bad_val"}] # No type or algoStatus
    
    asyncio.run(mock_account._fetch_exchange_order_snapshot())
    assert mock_account._reconciliation_status == "SUCCESS"
    # Should not crash


def test_normal_open_order_api_failure(mock_account):
    mock_account.positions["BTC/USDT"] = {"side": "LONG"}
    mock_account.exchange.fetch_open_orders.side_effect = Exception("Timeout")
    
    asyncio.run(mock_account._fetch_exchange_order_snapshot())
    assert mock_account._reconciliation_status == "ERROR"


def test_reconciliation_zero_modification(mock_account):
    mock_account.positions["BTC/USDT"] = {"side": "LONG"}
    asyncio.run(mock_account._fetch_exchange_order_snapshot())
    assert mock_account.exchange.create_order.call_count == 0
    assert mock_account.exchange.cancel_order.call_count == 0
    assert mock_account.exchange.cancel_all_orders.call_count == 0
    delete_calls = [c for c in mock_account.exchange.request.call_args_list if len(c[0]) > 2 and c[0][2] == "DELETE"]
    assert len(delete_calls) == 0
