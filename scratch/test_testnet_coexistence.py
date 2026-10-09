import asyncio
import os
from dotenv import load_dotenv
import ccxt.async_support as ccxt

load_dotenv()
api_key = os.getenv('TESTNET_API_KEY') or os.getenv('BINANCE_TESTNET_API_KEY')
secret = os.getenv('TESTNET_API_SECRET') or os.getenv('BINANCE_TESTNET_API_SECRET')

async def main():
    if not api_key:
        print("NO_TESTNET_CREDENTIALS")
        return
        
    exchange = ccxt.binanceusdm({
        'apiKey': api_key,
        'secret': secret,
        'enableRateLimit': True,
    })
    exchange.set_sandbox_mode(True)
    
    try:
        print("Creating tiny position on TESTNET...")
        # 1. Create a tiny position on 1000PEPEUSDT or something (LONG 1)
        sym = '1000PEPE/USDT:USDT'
        try:
            await exchange.create_order(sym, 'market', 'buy', 10)
        except Exception as e:
            print("Failed to create position:", e)
            return

        print("Position created. Adding protective STOP_MARKET...")
        # 2. Add first STOP_MARKET
        try:
            order1 = await exchange.create_order(sym, 'STOP_MARKET', 'sell', 10, None, {
                'stopPrice': 0.0010,
                'closePosition': True
            })
            print("ORDER1 created:", order1['id'])
        except Exception as e:
            print("ORDER1 failed:", e)
            return

        print("Adding SECOND protective STOP_MARKET (coexistence test)...")
        # 3. Add second STOP_MARKET
        try:
            order2 = await exchange.create_order(sym, 'STOP_MARKET', 'sell', 10, None, {
                'stopPrice': 0.0011,
                'closePosition': True
            })
            print("ORDER2 created:", order2['id'])
            print("COEXISTENCE: SUCCESS")
        except Exception as e:
            print("ORDER2 failed:", e)
            print("COEXISTENCE: FAILED")

        print("Testing editOrder (cancelReplace) on ORDER1...")
        try:
            # We want to change order1's stopPrice to 0.0012
            # Wait, CCXT requires amount for edit_order? Let's check ccxt source.
            edit = await exchange.edit_order(order1['id'], sym, 'STOP_MARKET', 'sell', 10, None, {
                'stopPrice': 0.0012,
                'closePosition': True
            })
            print("EDIT ORDER (cancelReplace): SUCCESS. New ID:", edit.get('id'))
        except Exception as e:
            print("EDIT ORDER (cancelReplace): FAILED:", e)

    finally:
        print("Cleaning up...")
        await exchange.cancel_all_orders(sym)
        # close position
        try:
            pos = await exchange.fetch_positions([sym])
            for p in pos:
                amt = float(p['contracts'])
                if amt > 0:
                    side = 'sell' if p['side'] == 'long' else 'buy'
                    await exchange.create_order(sym, 'market', side, amt)
        except Exception as e:
            pass
        await exchange.close()

asyncio.run(main())
