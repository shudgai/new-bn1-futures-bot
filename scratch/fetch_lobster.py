import ccxt
import time
ex = ccxt.binanceusdm()
try:
    print(ex.fetch_ohlcv('龙虾/USDT:USDT', '1m', limit=5))
except Exception as e:
    print("Error:", e)
