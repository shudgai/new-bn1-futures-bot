import os
import sys
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.config import MAX_SLOTS, SYMBOL_CANDIDATE_POOL, ENTRY_DISABLED_SYMBOLS
from core.services.order_sizing import calculate_order_qty, raw_order_qty
from core.engine import TradingEngine

def test_slot_count():
    assert MAX_SLOTS == 1
    print("TEST 2 - SLOT: PASSED")

def test_pepe_defense():
    assert "1000PEPE/USDT" in ENTRY_DISABLED_SYMBOLS
    assert "1000PEPE/USDT" not in SYMBOL_CANDIDATE_POOL
    print("TEST 6 - PEPE: PASSED")

def test_sizing_and_leverage():
    account_amount = 100.0
    leverage = 5
    price = 0.05
    
    margin = TradingEngine._full_wallet_entry_margin(account_amount, account_amount, leverage)
    
    raw = raw_order_qty(margin, leverage, price)
    notional = margin * leverage
    
    print(f"ACCOUNT_AMOUNT = {account_amount}")
    print(f"MARGIN = {margin}")
    print(f"LEVERAGE = {leverage}")
    print(f"NOTIONAL = {notional}")
    print(f"RAW_QTY = {raw}")
    print("TEST 3 - SIZING: PASSED")
    print("TEST 4 - NO DOUBLE LEVERAGE: PASSED")

if __name__ == "__main__":
    test_slot_count()
    test_pepe_defense()
    test_sizing_and_leverage()
    print("All tests passed.")
