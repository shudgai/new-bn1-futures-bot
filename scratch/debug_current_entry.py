import asyncio
from services.api import get_klines
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy

async def main():
    strategy = UnifiedEntryStrategy()
    for symbol in ["1000PEPE/USDT", "龙虾/USDT"]:
        try:
            klines = await get_klines(symbol, "1m", 100)
            if klines is not None and not klines.empty:
                price = float(klines.iloc[-1]["close"])
                for side in ["LONG", "SHORT"]:
                    result = strategy.evaluate_entry(klines, price, side)
                    print(f"[{symbol}] {side} Price: {price} -> Result: {result}")
        except Exception as e:
            print(f"[{symbol}] Error: {e}")

asyncio.run(main())
