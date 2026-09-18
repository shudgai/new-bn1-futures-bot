from core.engine import TradingEngine
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
import asyncio

async def test():
    engine = TradingEngine()
    df = await engine.fetch_klines("1000PEPE/USDT", "1m", 200)
    df = engine.strategy.compute_indicators(df)
    strategy = UnifiedEntryStrategy()
    res = strategy.evaluate_entry(df, float(df.iloc[-1]["close"]), "LONG")
    print("Result:", res)

asyncio.run(test())
