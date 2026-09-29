import asyncio
from ccxt.async_support import bingx

async def main():
    exchange = bingx({'enableRateLimit': True})
    await exchange.load_markets()
    market = exchange.markets['1000PEPE/USDT:USDT']
    print(f"contractSize: {market.get('contractSize')}")
    print(f"stepSize: {market['precision']['amount']}")
    amount_usdt = 30
    leverage = 10
    price = 0.00446
    qty = (amount_usdt * leverage) / max(price, 1e-12)
    print(f"Calculated qty: {qty}")
    print(f"qty to precision: {exchange.amount_to_precision('1000PEPE/USDT:USDT', qty)}")
    await exchange.close()

asyncio.run(main())
