import urllib.request
import json

url = "http://127.0.0.1:8006/api/klines?symbol=1000PEPE%2FUSDT&timeframe=1m&limit=10&include_live=true"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    resp = json.loads(response.read().decode())

klines = resp.get("data", resp)
if isinstance(klines, dict): klines = klines.get("data", [])

for k in klines[-5:]:
    print(f"Time: {k.get('timestamp')}, O: {k.get('open')}, C: {k.get('close')}, upper: {k.get('kc_upper')}, middle: {k.get('kc_middle')}, atr: {k.get('atr')}, H: {k.get('high')}, L: {k.get('low')}")

