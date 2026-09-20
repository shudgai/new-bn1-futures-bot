import requests
import json
import pandas as pd

res = requests.get('http://100.70.198.75:62232/api/klines?symbol=1000PEPE/USDT&timeframe=1m&limit=15')
data = res.json()
if 'data' in data:
    for d in data['data']:
        # calculate body
        body = abs(d['close'] - d['open'])
        range = d['high'] - d['low']
        # Convert ms to readable time using pd
        time_str = pd.to_datetime(d['timestamp'], unit='ms').strftime('%H:%M:%S')
        print(f"[{time_str}] Open:{d['open']:.5f} Close:{d['close']:.5f} Body:{body:.5f} ATR:{d.get('atr',0):.5f} KCMid:{d.get('kc_middle',0):.5f} MA15:{d.get('ma15',0):.5f} MA3:{d.get('ma3', d.get('ema_3',0)):.5f}")
else:
    print("No data")
