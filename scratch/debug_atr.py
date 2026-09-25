import urllib.request
import json
url = "http://127.0.0.1:8006/api/klines?symbol=1000PEPE%2FUSDT&timeframe=1m&limit=1"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    resp = json.loads(response.read().decode())

d = resp.get("data", resp)
if isinstance(d, dict): d = d.get("data", [])
if d:
    print(f"ATR: {d[0].get('atr')} (type: {type(d[0].get('atr'))})")
    print(f"MA3: {d[0].get('ma3')}, EMA20: {d[0].get('ema_20')}")

