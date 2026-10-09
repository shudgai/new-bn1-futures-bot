import sys
import os
import asyncio
import pandas as pd
sys.path.insert(0, os.path.abspath("."))
from core.services.symbol_runner import UnifiedSymbolRunner

async def main():
    class DummyContext:
        def __init__(self):
            self.exchange = None
    from core.testnet_account import TestnetAccount
    from core.exchange_client import UnifiedExchangeClient
    ex = UnifiedExchangeClient()
    acc = TestnetAccount(ex)
    runner = UnifiedSymbolRunner(symbol="NEIRO/USDT", max_leverage=10, account=acc, shared_context=DummyContext())
    await runner.initialize()
    df = runner.live_frame
    if df is not None and not df.empty:
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        cols = ['datetime', 'open', 'high', 'low', 'close', 'kc_upper', 'kc_middle', 'kc_lower', 'ma5', 'ma15', 'atr']
        print(df[cols].tail(15).to_string())
    else:
        print("Dataframe is empty or None")

asyncio.run(main())
