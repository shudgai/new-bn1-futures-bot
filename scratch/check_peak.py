import asyncio
import pandas as pd
from services.api import engine
from core.indicators import evaluate_signal

async def main():
    symbol = "1000PEPE/USDT"
    # Fetch enough klines to see the last 20 mins
    klines = await engine.client.get_klines(symbol, interval="1m", limit=30)
    df = pd.DataFrame(klines, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"])
    df["close"] = pd.to_numeric(df["close"])
    df["open"] = pd.to_numeric(df["open"])
    df["high"] = pd.to_numeric(df["high"])
    df["low"] = pd.to_numeric(df["low"])
    df["volume"] = pd.to_numeric(df["volume"])
    
    # We want to see the candles around that peak.
    # Let's just print the last 15 candles
    for i in range(len(df)-15, len(df)):
        last_close = df['close'].iloc[i]
        last_open = df['open'].iloc[i]
        last_high = df['high'].iloc[i]
        last_low = df['low'].iloc[i]
        prev_open = df['open'].iloc[i-1]
        prev_close = df['close'].iloc[i-1]
        
        is_red = last_close < last_open
        is_green = last_close > last_open
        is_bearish_engulfing = (prev_close > prev_open) and is_red and (last_close < prev_open) and (last_open >= prev_close)
        
        body = abs(last_close - last_open)
        upper_shadow = last_high - max(last_open, last_close)
        lower_shadow = min(last_open, last_close) - last_low
        is_bearish_pinbar = (upper_shadow > body * 2.0) and (lower_shadow < body)
        
        print(f"Index {i}: Open: {last_open:.6f}, Close: {last_close:.6f}, High: {last_high:.6f}, Low: {last_low:.6f}")
        print(f"  -> Red: {is_red}, Engulfing: {is_bearish_engulfing}, Pinbar: {is_bearish_pinbar}")
        print(f"  -> prev_close: {prev_close:.6f}, last_open: {last_open:.6f}, last_open >= prev_close: {last_open >= prev_close}")

if __name__ == "__main__":
    import os
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    asyncio.run(main())
