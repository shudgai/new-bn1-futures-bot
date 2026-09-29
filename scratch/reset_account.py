import asyncio
import os
import json
from dotenv import load_dotenv
import ccxt.async_support as ccxt

async def reset_account():
    load_dotenv()
    
    use_testnet = os.getenv("USE_TESTNET", "false").lower() == "true"
    api_key = os.getenv("BINANCE_API_KEY")
    secret = os.getenv("BINANCE_SECRET")
    
    # 1. 交易所部位與掛單清場
    exchange = ccxt.binanceusdm({
        'apiKey': api_key,
        'secret': secret,
        'enableRateLimit': True,
    })
    
    if use_testnet:
        exchange.set_sandbox_mode(True)
        
    try:
        print("Fetching open orders...")
        orders = await exchange.fetch_open_orders()
        for order in orders:
            symbol = order['symbol']
            order_id = order['id']
            print(f"Canceling order {order_id} for {symbol}")
            await exchange.cancel_order(order_id, symbol)
            
        print("Fetching positions...")
        positions = await exchange.fetch_positions()
        for pos in positions:
            symbol = pos['symbol']
            amt = float(pos.get('contracts', 0) or 0)
            side = pos.get('side')
            if amt > 0:
                print(f"Closing position for {symbol}: {side} {amt}")
                close_side = 'sell' if side == 'long' else 'buy'
                await exchange.create_order(
                    symbol=symbol,
                    type='market',
                    side=close_side,
                    amount=amt,
                    params={'reduceOnly': True}
                )
    except Exception as e:
        print(f"Error communicating with exchange: {e}")
    finally:
        await exchange.close()
        
    # 2. 本地緩存與狀態歸零
    files_to_remove = [
        'data/paper_account.json',
        'data/testnet_account.json',
        'data/ai_trade_analysis.json'
    ]
    for f in files_to_remove:
        if os.path.exists(f):
            os.remove(f)
            print(f"Deleted local cache: {f}")
            
    print("Cold reset complete.")

if __name__ == "__main__":
    asyncio.run(reset_account())
