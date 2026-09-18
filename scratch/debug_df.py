import pandas as pd
from core.services.api import BinanceAPI
api = BinanceAPI()
klines = api.get_klines("BTC/USDT", "1m", limit=10)
print("Columns:", klines.columns.tolist())
