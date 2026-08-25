import asyncio
import pandas as pd
from services.api import engine
from core.indicators import evaluate_signal

async def main():
    symbol = "1000PEPE/USDT"
    klines = await engine.client.get_klines(symbol, interval="1m", limit=5)
    df = pd.DataFrame(klines, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"])
    df["close"] = pd.to_numeric(df["close"])
    df["open"] = pd.to_numeric(df["open"])
    df["high"] = pd.to_numeric(df["high"])
    df["low"] = pd.to_numeric(df["low"])
    df["volume"] = pd.to_numeric(df["volume"])
    
    # Calculate MAs
    df['ma3'] = df['close'].rolling(window=3).mean()
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma25'] = df['close'].rolling(window=25).mean()
    
    last_close = float(df['close'].iloc[-1])
    last_open = float(df['open'].iloc[-1])
    last_high = float(df['high'].iloc[-1])
    last_low = float(df['low'].iloc[-1])
    prev_open = float(df['open'].iloc[-2])
    prev_close = float(df['close'].iloc[-2])
    is_red = last_close < last_open
    
    is_bearish_engulfing = (prev_close > prev_open) and is_red and (last_close < prev_open) and (last_open >= prev_close)
    body = abs(last_close - last_open)
    upper_shadow = last_high - max(last_open, last_close)
    lower_shadow = min(last_open, last_close) - last_low
    is_bearish_pinbar = (upper_shadow > body * 2.0) and (lower_shadow < body)
    
    print(f"is_red: {is_red}")
    print(f"is_bearish_engulfing: {is_bearish_engulfing}")
    print(f"is_bearish_pinbar: {is_bearish_pinbar}")
    print(f"ma5: {df['ma5'].iloc[-1]:.6f}, ma25: {df['ma25'].iloc[-1]:.6f}")
    
if __name__ == "__main__":
    import os
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    asyncio.run(main())
