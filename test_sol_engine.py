import asyncio
import pandas as pd
from core.strategy import SuperTrendKeltnerStrategy
import ccxt

async def main():
    exchange = ccxt.binance({'enableRateLimit': True})
    symbol = "SOL/USDT"
    
    # fetch 5m and 1h
    ohlcv_5m = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
    df_5m = pd.DataFrame(ohlcv_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_5m['timestamp'] = pd.to_datetime(df_5m['timestamp'], unit='ms')
    
    ohlcv_1h = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=200)
    df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_1h['timestamp'] = pd.to_datetime(df_1h['timestamp'], unit='ms')
    
    strategy = SuperTrendKeltnerStrategy()
    
    computed_1h = strategy.compute_indicators(df_1h.copy())
    st_direction_1h = int(computed_1h.iloc[-1]['st_direction'])
    ema_50_1h = float(df_1h["close"].ewm(span=50, adjust=False).mean().iloc[-1])
    trend_1h_declining = False # simplify for now
    
    print(f"1H st_direction: {st_direction_1h}")
    print(f"1H ema_50: {ema_50_1h}")
    
    res = strategy.evaluate_signal(
        df_5m, 
        symbol=symbol, 
        ema_50_1h=ema_50_1h,
        st_direction_1h=st_direction_1h,
        trend_1h_declining=trend_1h_declining,
        indicators_precomputed=False
    )
    print("Result:", res)

asyncio.run(main())
