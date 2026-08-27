import asyncio
import pandas as pd
from core.strategy import SuperTrendKeltnerStrategy
import ccxt

async def main():
    exchange = ccxt.binance({'enableRateLimit': True})
    symbol = "SOL/USDT"
    
    print(f"Fetching data for {symbol}...")
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    
    strategy = SuperTrendKeltnerStrategy()
    print("Evaluating signal...")
    try:
        res = strategy.evaluate_signal(df, symbol=symbol, indicators_precomputed=False)
        print("Result:")
        print(res)
    except Exception as e:
        print("Error:", e)

asyncio.run(main())
