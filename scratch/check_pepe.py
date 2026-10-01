import requests

data = requests.get('http://127.0.0.1:8006/api/klines?symbol=1000PEPE/USDT&timeframe=1m&limit=200').json()
if 'data' in data: data = data['data']

for row in data[-20:]:
    span = float(row['high']) - float(row['low'])
    if span == 0: span = 1e-9
    body = float(row['close']) - float(row['open'])
    ratio = abs(body) / span
    kc = float(row.get('kc_upper') or 999999)
    is_break = float(row['close']) > kc
    is_solid = ratio >= 0.35
    print(f"Time: {row['time']}, O:{row['open']}, C:{row['close']}, H:{row['high']}, L:{row['low']} | Ratio: {ratio:.3f}, Solid: {is_solid}, Breakout: {is_break}, Green: {body>0}")
