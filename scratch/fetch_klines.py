import json, urllib.request

url = "http://127.0.0.1:8006/api/klines?symbol=%E9%BE%99%E8%99%BE%2FUSDT&timeframe=1m&limit=15&include_live=true"
try:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        
        # Look around 1790910000000 (11:00)
        target = 1790910000000.0
        
        for k in data.get('klines', []):
            if abs(k['timestamp'] - target) <= 180000:
                print(f"Time: {k['timestamp']} O: {k['open']} H: {k['high']} L: {k['low']} C: {k['close']}")
                print(f"  MA5: {k.get('ma5', k.get('ma3'))} MA15: {k.get('ma15')} KC_MID: {k.get('kc_middle')}")
except Exception as e:
    print(f"Error: {e}")
