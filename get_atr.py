import asyncio
from core.engine import TradingEngine
async def main():
    e = TradingEngine(paper_trading=True)
    await e.initialize()
    df = await e.fetch_klines("1000PEPE/USDT", timeframe='1m', limit=200, keep_live=True)
    df = e.strategy.compute_indicators(df)
    print(f"Latest ATR: {df['atr'].iloc[-1]:.6f}")
    print(f"Latest MA3: {df['ma3'].iloc[-1]:.6f}")
asyncio.run(main())
