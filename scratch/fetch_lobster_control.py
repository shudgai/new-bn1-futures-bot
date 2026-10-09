import ccxt
import datetime

ex = ccxt.binanceusdm()
sym = '龙虾/USDT:USDT'

# We want 10/03 23:51 UTC to 23:58 UTC
# Let's just fetch limit=1500 and filter
klines = ex.fetch_ohlcv(sym, '1m', limit=1500)
for k in klines:
    ts = k[0]
    dt = datetime.datetime.fromtimestamp(ts/1000, tz=datetime.timezone.utc)
    if dt.year == 2026 and dt.month == 10 and dt.day == 3 and dt.hour == 23 and dt.minute >= 50:
        print(f"{ts},{k[1]},{k[2]},{k[3]},{k[4]},{k[5]}")
