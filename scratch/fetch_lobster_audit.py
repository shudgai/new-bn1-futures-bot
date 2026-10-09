import ccxt
import pandas as pd

try:
    exchange = ccxt.binanceusdm()
    ohlcv = exchange.fetch_ohlcv('NEIROUSDT', timeframe='1m', limit=30)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    print(df.tail(20).to_string())
except Exception as e:
    print(f"Error: {e}")
