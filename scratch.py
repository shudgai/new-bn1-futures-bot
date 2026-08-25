import asyncio
import pandas as pd
from core.api import BinanceAPI
from core.indicators import detect_ma5_ma25_cross_and_turn

async def main():
    api = BinanceAPI()
    klines = await api.get_klines("1000PEPEUSDT", "1m", limit=50)
    df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
    # Convert types as needed by indicators, though detect_ma... expects string or float.
    # Actually detect_ma... computes ADX internally, let's just pass the df as returned by the actual bot.
    # Wait, the bot's engine uses `fetch_klines` which does this conversion. Let's just use self.account or similar if we can, 
    # but the simplest is just to print the raw df after basic conversion.
