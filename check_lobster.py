import requests
import json
import urllib.parse
import pandas as pd

symbol = "1000PEPE/USDT"
url = f"http://127.0.0.1:8006/api/klines?symbol={urllib.parse.quote(symbol)}&timeframe=1m&limit=20&include_live=true"
try:
    r = requests.get(url)
    data = r.json()
    for row in data[-20:]:
        t = pd.to_datetime(row.get('timestamp'), unit='ms')
        c = float(row.get('close', 0))
        ma3 = float(row.get('ma3', 0))
        kcu = row.get('kc_upper')
        print(f"Time: {t}, C: {c:.6f}, MA3: {ma3:.6f}, KCU: {kcu}")
except Exception as e:
    print(e)
