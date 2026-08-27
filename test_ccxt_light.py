import asyncio
import ccxt.async_support as ccxt
import time

async def main():
    exchange = ccxt.binanceusdm({'enableRateLimit': True})
    await exchange.load_markets()
    try:
        tickers = await exchange.fetch_bids_asks(["BTC/USDT:USDT", "ETH/USDT:USDT"])
        print(tickers.keys())
        print(tickers["BTC/USDT:USDT"])
    except Exception as e:
        print("fetch_bids_asks error:", e)
    await exchange.close()

asyncio.run(main())
