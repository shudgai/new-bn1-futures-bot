import asyncio
import ccxt.async_support as ccxt
import time

async def main():
    exchange = ccxt.binanceusdm({'enableRateLimit': True})
    
    start = time.time()
    await exchange.load_markets()
    print("load_markets:", time.time() - start)
    
    start = time.time()
    tickers = await exchange.fetch_tickers(["BTC/USDT", "ETH/USDT"])
    print("fetch_tickers time:", time.time() - start, "Rate limit wait time?")
    
    await exchange.close()

asyncio.run(main())
