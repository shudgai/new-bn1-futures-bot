import asyncio
import pandas as pd
import ccxt.async_support as ccxt

async def main():
    exchange = ccxt.binanceusdm()
    symbol = "1000PEPE/USDT"
    klines = await exchange.fetch_ohlcv(symbol, timeframe="1m", limit=15)
    await exchange.close()
    df = pd.DataFrame(klines, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df['ma3'] = df['close'].rolling(window=3).mean()
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma25'] = df['close'].rolling(window=25).mean()
    
    for i in range(5, len(df)):
        ts = pd.to_datetime(df['timestamp'].iloc[i], unit='ms', utc=True).tz_convert('Asia/Taipei').strftime('%H:%M:%S')
        c = df['close'].iloc[i]
        o = df['open'].iloc[i]
        color = "GREEN" if c > o else "RED  "
        ma3 = df['ma3'].iloc[i]
        ma3_p = df['ma3'].iloc[i-1]
        ma3_p2 = df['ma3'].iloc[i-2]
        ma5 = df['ma5'].iloc[i]
        ma25 = df['ma25'].iloc[i]
        
        is_red = c < o
        is_green = c > o
        prev_c = df['close'].iloc[i-1]
        prev_o = df['open'].iloc[i-1]
        is_prev_red = prev_c < prev_o
        is_prev_green = prev_c > prev_o
        
        is_prev2_c = df['close'].iloc[i-2]
        is_prev2_o = df['open'].iloc[i-2]
        is_prev2_red = is_prev2_c < is_prev2_o
        is_prev2_green = is_prev2_c > is_prev2_o
        
        is_peak = (ma3 < ma3_p) and (ma3_p >= ma3_p2)
        is_trough = (ma3 > ma3_p) and (ma3_p <= ma3_p2)
        ma3_dir = "UP  " if ma3 > ma3_p else "DOWN"
        trend = "UP" if ma5 > ma25 else "DN"
        
        print(f"[{ts}] {color} | Tr:{trend} MA3:{ma3_dir} Pk:{is_peak} Tr:{is_trough} | R1:{is_red} R2:{is_prev_red} R3:{is_prev2_red} | O:{o:.6f} C:{c:.6f}")

if __name__ == "__main__":
    asyncio.run(main())
