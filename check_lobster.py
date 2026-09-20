import urllib.request
import json
data = json.loads(urllib.request.urlopen('http://127.0.0.1:8006/api/status').read().decode('utf-8'))
for sym, mode in data.get('market_modes', {}).items():
    if "龙虾" in sym:
        print(f"{sym} Mode: {mode}")

for sym, direction in data.get('symbol_directions', {}).items():
    if "龙虾" in sym:
        print(f"{sym} Direction: {direction}")

for sym in data.get('volatility_stats', {}):
    if "龙虾" in sym:
        print(f"{sym} Volatility: {data['volatility_stats'][sym]}")
        
print("Trades:")
for t in data.get('trades', [])[-5:]:
    if "龙虾" in t.get('symbol', ''):
        print(t)
