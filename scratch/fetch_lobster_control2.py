import ccxt
import datetime

ex = ccxt.binanceusdm()
sym = '龙虾/USDT:USDT'

klines = ex.fetch_ohlcv(sym, '1m', limit=1500)
for k in klines:
    ts = k[0]
    dt = datetime.datetime.fromtimestamp(ts/1000, tz=datetime.timezone.utc)
    if dt.year == 2026 and dt.month == 10 and dt.day == 4 and dt.hour == 0 and dt.minute <= 5:
        print(f"{ts},{k[1]},{k[2]},{k[3]},{k[4]},{k[5]}")
