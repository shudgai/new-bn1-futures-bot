import json, urllib.request

def check():
    data = json.loads(urllib.request.urlopen('http://127.0.0.1:8006/api/status').read().decode('utf-8'))
    for l in data.get('logs', []):
        if "market_crash" in str(l) or "cooldown" in str(l) or "halt" in str(l).lower():
            print(l)

check()
