import asyncio
import httpx
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy

async def get_klines_fix(symbol):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol.replace('/', '')}&interval=1m&limit=100"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
    data = response.json()
    import pandas as pd
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_asset_volume", "number_of_trades", "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"])
    from core.indicators import add_all_indicators
    return add_all_indicators(df)

async def main():
    strategy = UnifiedEntryStrategy()
    for symbol in ["1000PEPE/USDT"]:
        try:
            klines = await get_klines_fix(symbol)
            if klines is not None and not klines.empty:
                price = float(klines.iloc[-1]["close"])
                for side in ["LONG", "SHORT"]:
                    result = strategy.evaluate_entry(klines, price, side)
                    print(f"[{symbol}] {side} Price: {price} -> Result: {result}")
        except Exception as e:
            print(f"[{symbol}] Error: {e}")

asyncio.run(main())
