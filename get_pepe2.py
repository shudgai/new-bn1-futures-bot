import requests
res = requests.get('http://127.0.0.1:8006/api/klines?symbol=1000PEPE/USDT&timeframe=1m&limit=15')
data = res.json()
if 'data' in data:
    for d in data['data']:
        body = abs(d['close'] - d['open'])
        print(f"Time: {d['timestamp']}, Open:{d['open']:.5f} Close:{d['close']:.5f} Body:{body:.5f} ATR:{d.get('atr',0):.5f}")
else:
    print("No data")
