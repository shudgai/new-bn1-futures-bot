import asyncio
import ccxt.async_support as ccxt

async def main():
    exchange = ccxt.binanceusdm()
    await exchange.load_markets()
    symbols = ["BTC/USDT", "ETH/USDT"]
    try:
        tickers = await exchange.fetch_bids_asks(symbols)
        print("Keys:", list(tickers.keys()))
        print("Values:", tickers)
    except Exception as e:
        print("Error:", e)
    finally:
        await exchange.close()

asyncio.run(main())
