import requests
import urllib.parse
import pandas as pd
symbol = "1000PEPE/USDT"
url = f"http://127.0.0.1:8006/api/klines?symbol={urllib.parse.quote(symbol)}&timeframe=1m&limit=100&include_live=true"
r = requests.get(url)
data = r.json()
if isinstance(data, dict) and "data" in data:
    data = data["data"]
for row in data:
    t = pd.to_datetime(row.get('timestamp'), unit='ms', utc=True).tz_convert('Asia/Taipei')
    if t.hour == 9 and t.minute >= 43 and t.minute <= 48:
        print(f"Time: {t.strftime('%H:%M:%S')}, O: {row.get('open')}, C: {row.get('close')}, KCU: {row.get('kc_upper')}, KCM: {row.get('kc_middle')}, ATR: {row.get('atr')}")
