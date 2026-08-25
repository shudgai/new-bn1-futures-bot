import asyncio
import pandas as pd
from services.api import engine
from core.indicators import evaluate_signal

async def main():
    symbol = "1000PEPE/USDT"
    klines = await engine.client.get_klines(symbol, interval="1m", limit=30)
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
    
    ma3_curr = df['ma3'].iloc[-1]
    ma3_prev = df['ma3'].iloc[-2]
    ma3_prev2 = df['ma3'].iloc[-3]
    
    is_peak = (ma3_curr < ma3_prev) and (ma3_prev > ma3_prev2)
    is_trough = (ma3_curr > ma3_prev) and (ma3_prev < ma3_prev2)
    
    print(f"ma3_curr: {ma3_curr:.6f}, ma3_prev: {ma3_prev:.6f}, ma3_prev2: {ma3_prev2:.6f}")
    print(f"is_peak: {is_peak}, is_trough: {is_trough}")
    
    signal = evaluate_signal(df, symbol)
    print(f"Signal evaluation: {signal}")

asyncio.run(main())
