import asyncio
import ccxt.async_support as ccxt

async def main():
    exchange = ccxt.binanceusdm()
    try:
        await exchange.fetch_ticker('BTC/USDT')
        headers = exchange.last_response_headers
        print("HEADERS:")
        for k, v in headers.items():
            if 'weight' in k.lower():
                print(f"{k}: {v}")
    finally:
        await exchange.close()

asyncio.run(main())
