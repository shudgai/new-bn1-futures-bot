import asyncio
from core.api import BinanceAPI
from core.indicators import TradingStrategy

async def test():
    api = BinanceAPI()
    df = await api.get_klines_df("1000PEPEUSDT", "1m", limit=10)
    print(df.tail(3))
    await api.close()

asyncio.run(test())
