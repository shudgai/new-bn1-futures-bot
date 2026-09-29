import asyncio
from ccxt.async_support import binanceusdm

async def main():
    exchange = binanceusdm({'enableRateLimit': True})
    await exchange.load_markets()
    market = exchange.markets['1000PEPE/USDT']
    print(f"contractSize: {market.get('contractSize')}")
    print(f"stepSize: {market['precision']['amount']}")
    print(f"amount_to_precision(30.9958): {exchange.amount_to_precision('1000PEPE/USDT', 30.9958)}")
    amount_usdt = 30
    leverage = 10
    price = 0.00446
    qty = (amount_usdt * leverage) / max(price, 1e-12)
    print(f"Calculated qty: {qty}")
    print(f"qty to precision: {exchange.amount_to_precision('1000PEPE/USDT', qty)}")
    await exchange.close()

asyncio.run(main())
