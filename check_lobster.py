import requests
import json
import urllib.parse

symbol = "龙虾/USDT"
url = f"http://127.0.0.1:8006/api/klines?symbol={urllib.parse.quote(symbol)}&timeframe=1m&limit=5"
try:
    r = requests.get(url)
    data = r.json()
    for row in data:
        t = row.get("timestamp")
        o = row.get("open")
        c = row.get("close")
        h = row.get("high")
        l = row.get("low")
        kcu = row.get("kc_upper")
        kcd = row.get("kc_lower")
        ma3 = row.get("ma3")
        atr = row.get("atr")
        print(f"Time: {t}, O: {o}, C: {c}, KCU: {kcu}, KCD: {kcd}, MA3: {ma3}, ATR: {atr}")
except Exception as e:
    print(e)
