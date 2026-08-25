import asyncio
import pandas as pd
from services.api import engine

async def main():
    symbol = "1000PEPE/USDT"
    klines = await engine.client.get_klines(symbol, interval="1m", limit=30)
    df = pd.DataFrame(klines, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"])
    df["close"] = pd.to_numeric(df["close"])
    df["open"] = pd.to_numeric(df["open"])
    df["high"] = pd.to_numeric(df["high"])
    df["low"] = pd.to_numeric(df["low"])
    df["volume"] = pd.to_numeric(df["volume"])
    
    # We want to see the candles around 12:06 UTC
    for i in range(1, len(df)):
        ts = pd.to_datetime(df['timestamp'].iloc[i], unit='ms')
        if ts.hour == 12 and ts.minute in [5, 6, 7]:
            last_close = df['close'].iloc[i]
            last_open = df['open'].iloc[i]
            last_high = df['high'].iloc[i]
            last_low = df['low'].iloc[i]
            prev_open = df['open'].iloc[i-1]
            prev_close = df['close'].iloc[i-1]
            prev_high = df['high'].iloc[i-1]
            prev_low = df['low'].iloc[i-1]
            
            is_green = last_close > last_open
            tolerance = last_close * 0.0001
            is_bullish_engulfing = (prev_close < prev_open) and is_green and (last_close > prev_open) and (last_open <= prev_close + tolerance)
            
            body = abs(last_close - last_open)
            upper_shadow = last_high - max(last_open, last_close)
            lower_shadow = min(last_open, last_close) - last_low
            is_bullish_pinbar = (lower_shadow > body * 2.0) and (upper_shadow < body)
            
            prev_body = abs(prev_close - prev_open)
            prev_upper_shadow = prev_high - max(prev_open, prev_close)
            prev_lower_shadow = min(prev_open, prev_close) - prev_low
            prev_is_bullish_pinbar = (prev_lower_shadow > prev_body * 2.0) and (prev_upper_shadow < prev_body)
            
            print(f"[{ts}] O:{last_open:.6f} C:{last_close:.6f} H:{last_high:.6f} L:{last_low:.6f}")
            print(f"  -> Green: {is_green}, Engulf: {is_bullish_engulfing}, Pinbar: {is_bullish_pinbar}, PrevPin: {prev_is_bullish_pinbar}")

if __name__ == "__main__":
    import os
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    asyncio.run(main())
