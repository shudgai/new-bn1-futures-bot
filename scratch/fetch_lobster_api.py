import requests
import urllib.parse
import pandas as pd
import json

symbol = "龙虾/USDT"
url = f"http://127.0.0.1:8006/api/klines?symbol={urllib.parse.quote(symbol)}&timeframe=1m&limit=30&include_live=true"
try:
    r = requests.get(url)
    data = r.json()
    print("Type:", type(data))
    if isinstance(data, dict):
        print("Keys:", data.keys())
        if "detail" in data:
            print("Error:", data["detail"])
        elif "data" in data:
            data = data["data"]
            
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                t = pd.to_datetime(row.get('timestamp'), unit='ms')
                print(f"Time: {t}, O: {row.get('open')} H: {row.get('high')} L: {row.get('low')} C: {row.get('close')}, MA3: {row.get('ma3')}, MA5: {row.get('ma5')}")
            else:
                print("Row is not dict:", type(row), row)
    else:
        print(data)
except Exception as e:
    print(e)
