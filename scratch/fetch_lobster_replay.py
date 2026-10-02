import json, urllib.request

url = "http://127.0.0.1:8006/api/klines?symbol=%E9%BE%99%E8%99%BE%2FUSDT&timeframe=1m&limit=10&include_live=true"
try:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        print(f"Latest timestamp: {data['klines'][-1]['timestamp']}")
        for k in data['klines']:
            print(f"T: {k['timestamp']} C: {k['close']}")
except Exception as e:
    print(f"Error: {e}")
