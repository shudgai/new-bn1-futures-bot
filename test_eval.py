import asyncio
import pandas as pd
from core.strategy import evaluate_signal, detect_ma5_reversal
import ccxt

async def main():
    exchange = ccxt.binance({'enableRateLimit': True})
    symbol = "1000PEPE/USDT"
    
    # fetch klines
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=50)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    
    # check detect_ma5_reversal
    sig_long = detect_ma5_reversal(df, 'LONG')
    sig_short = detect_ma5_reversal(df, 'SHORT')
    
    print(f"LONG Reversal Signal: {sig_long.get('detected')} - {sig_long.get('reason')}")
    print(f"SHORT Reversal Signal: {sig_short.get('detected')} - {sig_short.get('reason')}")
    
    # calculate indicators
    df['atr'] = df['high'] - df['low'] # dummy atr
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma25'] = df['close'].rolling(window=25).mean()
    
    # check evaluate_signal (requires full indicators)
    # Actually evaluate_signal is big. Let's just print the reversal result.
    
    if len(df) >= 5:
        ma5_last_3 = df['ma5'].tail(3).values
        print(f"MA5 last 3 values: {ma5_last_3}")

asyncio.run(main())
