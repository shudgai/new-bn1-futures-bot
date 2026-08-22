import asyncio
from core.binance_client import BinanceClient
from core.strategy import detect_ma7_reversal
import pandas as pd

async def main():
    client = BinanceClient()
    for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "AVAX/USDT"]:
        df = await client.fetch_klines(symbol, "1m", limit=30)
        ma7 = float(df['close'].rolling(7).mean().iloc[-1])
        ma25 = float(df['close'].rolling(25).mean().iloc[-1])
        current_direction = "LONG" if ma7 > ma25 else "SHORT"
        
        sig = detect_ma7_reversal(df, side=current_direction, live_price=float(df['close'].iloc[-1]))
        print(f"{symbol} ({current_direction}): {sig['detected']} - {sig.get('reason', '')}")

asyncio.run(main())
