import asyncio
import pandas as pd
from core.api import BinanceAPI

async def main():
    api = BinanceAPI()
    klines = await api.get_klines("1000PEPEUSDT", "5m", limit=20)
    df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Taipei')
    df['close'] = df['close'].astype(float)
    df['open'] = df['open'].astype(float)
    df['ma3'] = df['close'].rolling(3).mean()
    print(df[['timestamp', 'open', 'close', 'ma3']].tail(10))

asyncio.run(main())
