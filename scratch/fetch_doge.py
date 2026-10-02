import requests
from datetime import datetime

symbol = "DOGEUSDT"
start_time = 1790898900000  # 2026-10-01 23:55:00 UTC
end_time = 1790899380000    # 2026-10-02 00:03:00 UTC

url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1m&startTime={start_time}&endTime={end_time}&limit=10"
r = requests.get(url)
for k in r.json():
    t = datetime.fromtimestamp(k[0]/1000).strftime('%Y-%m-%d %H:%M:%S UTC')
    print(f"{t} (ts={k[0]}): Open={k[1]}, High={k[2]}, Low={k[3]}, Close={k[4]}")
