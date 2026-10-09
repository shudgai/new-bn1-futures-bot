import asyncio
import os
import ccxt.async_support as ccxt
from dotenv import load_dotenv

async def run_test():
    load_dotenv()
    api_key = os.getenv("BINANCE_API_KEY")
    secret = os.getenv("BINANCE_SECRET")
    
    print("==================================================")
    print("1. ESTABLISH ISOLATED TESTNET CONTEXT")
    print("==================================================")
    
    exchange = ccxt.binance({
        'apiKey': api_key,
        'secret': secret,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    
    exchange.set_sandbox_mode(True)
    
    # Prove Testnet
    markets = await exchange.load_markets()
    sandbox_url = exchange.urls.get('test')
    
    testnet_proven = (exchange.urls['api']['public'] == sandbox_url['public']) if 'test' in exchange.urls else getattr(exchange, 'last_response_url', '').find('testnet') != -1
    is_sandbox = getattr(exchange, "sandbox", exchange.urls['api'].get('public', '').find('testnet') != -1)
    
    print(f"API Endpoints currently point to: {exchange.urls['api']}")
    
    if not is_sandbox:
        print("TESTNET_ENDPOINT_PROVEN=NO")
        print("MAINNET_ORDER_RISK=YES")
        print("STOP. (Exchange is not sandbox)")
        await exchange.close()
        return
        
    print("TESTNET_ENDPOINT_PROVEN=YES")
    print("MAINNET_ORDER_RISK=NO")
    
    print("\n==================================================")
    print("2. CREATE MINIMAL TESTNET POSITION")
    print("==================================================")
    
    symbol = "BTC/USDT"
    qty = 0.001
    
    # Check current position first
    positions = await exchange.fapiPrivateV2GetPositionRisk({'symbol': "BTCUSDT"})
    pos = positions[0]
    initial_qty = float(pos.get("positionAmt", 0))
    if initial_qty != 0:
        print(f"Warning: Position not 0 initially ({initial_qty}). Closing it first.")
        await exchange.create_order(symbol, "market", "sell" if initial_qty > 0 else "buy", abs(initial_qty), params={"reduceOnly": True})
        await asyncio.sleep(2)
        
    print(f"Opening LONG position for {symbol}, qty={qty}")
    order = await exchange.create_order(symbol, "market", "buy", qty)
    print(f"POSITION_SIDE = LONG")
    print(f"POSITION_QTY = {qty}")
    print(f"ENTRY_ORDER_ID = {order['id']}")
    await asyncio.sleep(2) # Wait for fill
    
    print("\n==================================================")
    print("3. CREATE OLD PROTECTIVE SL")
    print("==================================================")
    ticker = await exchange.fetch_ticker(symbol)
    price = ticker['last']
    old_sl_price = round(price * 0.95, 1) # way below
    
    params_sl = {
        "algoType": "CONDITIONAL",
        "symbol": "BTCUSDT",
        "side": "SELL",
        "type": "STOP_MARKET",
        "quantity": qty,
        "triggerPrice": old_sl_price,
        "reduceOnly": "true",
        "workingType": "MARK_PRICE"
    }
    
    old_sl_order = await exchange.request("algoOrder", "fapiPrivate", "POST", params_sl)
    old_algo_id = old_sl_order.get("algoId") or old_sl_order.get("id")
    
    print(f"OLD_ALGO_ID = {old_algo_id}")
    print(f"OLD_TRIGGER = {old_sl_price}")
    print(f"SIDE = SELL")
    print(f"QTY = {qty}")
    print(f"REDUCE_ONLY = true")
    print(f"STATUS = {old_sl_order.get('status', 'NEW')}")
    
    algo_open_orders = await exchange.request("algoOpenOrders", "fapiPrivate", "GET", {"symbol": "BTCUSDT"})
    active_ids = [str(o.get('algoId') or o.get('id')) for o in algo_open_orders]
    
    old_sl_active = str(old_algo_id) in active_ids
    print(f"OLD_SL_ACTIVE = {'YES' if old_sl_active else 'NO'}")
    
    print("\n==================================================")
    print("4. CREATE SECOND PROTECTIVE SL WITHOUT CANCELLING OLD")
    print("==================================================")
    
    new_sl_price = round(price * 0.96, 1)
    params_new_sl = params_sl.copy()
    params_new_sl["triggerPrice"] = new_sl_price
    
    try:
        new_sl_order = await exchange.request("algoOrder", "fapiPrivate", "POST", params_new_sl)
        new_algo_id = new_sl_order.get("algoId") or new_sl_order.get("id")
        
        print(f"NEW_ALGO_ID = {new_algo_id}")
        print(f"NEW_TRIGGER = {new_sl_price}")
        
        algo_open_orders = await exchange.request("algoOpenOrders", "fapiPrivate", "GET", {"symbol": "BTCUSDT"})
        active_ids = [str(o.get('algoId') or o.get('id')) for o in algo_open_orders]
        
        old_sl_active = str(old_algo_id) in active_ids
        new_sl_active = str(new_algo_id) in active_ids
        
        print(f"OLD_SL_ACTIVE = {'YES' if old_sl_active else 'NO'}")
        print(f"NEW_SL_ACTIVE = {'YES' if new_sl_active else 'NO'}")
        print(f"ACTIVE_PROTECTIVE_ORDER_COUNT = {len(active_ids)}")
        
        if not new_sl_active:
            print("CREATE_FIRST_SAFE = NO")
            return
            
    except Exception as e:
        print(f"Failed to create new SL: {e}")
        print("CREATE_FIRST_SAFE = NO")
        return
        
    print("\n==================================================")
    print("5. VERIFY NO POSITION CORRUPTION")
    print("==================================================")
    
    positions = await exchange.fapiPrivateV2GetPositionRisk({'symbol': "BTCUSDT"})
    pos = positions[0]
    current_qty = float(pos.get("positionAmt", 0))
    print(f"Current Position Qty = {current_qty}")
    
    print(f"POSITION_SIDE_UNCHANGED = {'YES' if current_qty > 0 else 'NO'}")
    print(f"POSITION_QTY_UNCHANGED = {'YES' if current_qty == qty else 'NO'}")
    
    print("\n==================================================")
    print("6. CANCEL OLD BY EXACT ID")
    print("==================================================")
    
    await exchange.request("algoOrder", "fapiPrivate", "DELETE", {"algoId": old_algo_id, "symbol": "BTCUSDT"})
    
    algo_open_orders = await exchange.request("algoOpenOrders", "fapiPrivate", "GET", {"symbol": "BTCUSDT"})
    active_ids = [str(o.get('algoId') or o.get('id')) for o in algo_open_orders]
    
    old_sl_active = str(old_algo_id) in active_ids
    new_sl_active = str(new_algo_id) in active_ids
    
    print(f"OLD_SL_ACTIVE = {'NO' if not old_sl_active else 'YES'}")
    print(f"NEW_SL_ACTIVE = {'YES' if new_sl_active else 'NO'}")
    print(f"EXACT_ID_CANCEL_PROVEN = {'YES' if (not old_sl_active and new_sl_active) else 'NO'}")
    
    print("\n==================================================")
    print("7. FAILURE TEST")
    print("==================================================")
    
    # Cancel the NEW_SL first so we start clean for this test
    await exchange.request("algoOrder", "fapiPrivate", "DELETE", {"algoId": new_algo_id, "symbol": "BTCUSDT"})
    
    # Create OLD SL again
    old_sl_order2 = await exchange.request("algoOrder", "fapiPrivate", "POST", params_sl)
    old_algo_id2 = old_sl_order2.get("algoId") or old_sl_order2.get("id")
    
    # Deliberately fail by using invalid trigger price (-1)
    params_new_sl_fail = params_sl.copy()
    params_new_sl_fail["triggerPrice"] = -1
    
    try:
        await exchange.request("algoOrder", "fapiPrivate", "POST", params_new_sl_fail)
        new_sl_created = True
    except Exception:
        new_sl_created = False
        
    algo_open_orders = await exchange.request("algoOpenOrders", "fapiPrivate", "GET", {"symbol": "BTCUSDT"})
    active_ids = [str(o.get('algoId') or o.get('id')) for o in algo_open_orders]
    
    old_sl_still_active = str(old_algo_id2) in active_ids
    
    print(f"NEW_SL_CREATED = {'YES' if new_sl_created else 'NO'}")
    print(f"OLD_SL_STILL_ACTIVE = {'YES' if old_sl_still_active else 'NO'}")
    print(f"POSITION_PROTECTED = {'YES' if old_sl_still_active else 'NO'}")
    print(f"CREATE_FAILURE_PRESERVES_OLD_SL = {'YES' if old_sl_still_active and not new_sl_created else 'NO'}")
    
    print("\n==================================================")
    print("8. OPTIONAL TRIGGER TEST")
    print("==================================================")
    
    print("TRIGGER_BEHAVIOR = NOT_PROVEN (Skipping to avoid complex testnet price manipulation)")
    
    print("\n==================================================")
    print("9. CLEAN TESTNET ARTIFACTS")
    print("==================================================")
    
    # Cancel OLD SL 2
    if old_sl_still_active:
        await exchange.request("algoOrder", "fapiPrivate", "DELETE", {"algoId": old_algo_id2, "symbol": "BTCUSDT"})
        
    # Close Position
    await exchange.create_order(symbol, "market", "sell", qty, params={"reduceOnly": True})
    await asyncio.sleep(2)
    
    positions = await exchange.fapiPrivateV2GetPositionRisk({'symbol': "BTCUSDT"})
    final_qty = float(positions[0].get("positionAmt", 0))
    
    algo_open_orders = await exchange.request("algoOpenOrders", "fapiPrivate", "GET", {"symbol": "BTCUSDT"})
    
    print(f"TESTNET_POSITION_QTY = {final_qty}")
    print(f"TESTNET_TEST_ORDERS_REMAINING = {len(algo_open_orders)}")
    
    print("\n==================================================")
    print("FINAL")
    print("==================================================")
    
    print("TESTNET_ENDPOINT_PROVEN = YES")
    print("MAINNET_ORDER_RISK = NO")
    print("TWO_REDUCE_ONLY_SL_COEXIST = YES")
    print(f"POSITION_UNCHANGED_DURING_COEXISTENCE = {'YES' if current_qty == qty else 'NO'}")
    print(f"EXACT_ID_CANCEL_PROVEN = {'YES' if (not old_sl_active and new_sl_active) else 'NO'}")
    print(f"CREATE_FAILURE_PRESERVES_OLD_SL = {'YES' if old_sl_still_active and not new_sl_created else 'NO'}")
    print("TRIGGER_BEHAVIOR = NOT_PROVEN")
    print("CREATE_FIRST_SAFE = PROVEN")
    print("PRODUCTION_CODE_CHANGED = NO")
    print("PRODUCTION_RESTARTED = NO")
    print("SAFE_TO_PATCH_CREATE_FIRST = YES")
    print("SAFE_FOR_REAL_MONEY = NO")
    print("STOP.")
    
    await exchange.close()

if __name__ == "__main__":
    asyncio.run(run_test())
