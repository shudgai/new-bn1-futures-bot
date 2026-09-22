import requests, urllib.parse, pandas as pd

symbol = "1000PEPE/USDT"
url = f"http://127.0.0.1:8006/api/klines?symbol={urllib.parse.quote(symbol)}&timeframe=1m&limit=30"
try:
    r = requests.get(url)
    data = r.json()
    df = pd.DataFrame(data)
    df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
    for _, row in df.iterrows():
        t = pd.to_datetime(row['timestamp'], unit='ms')
        c = row['close']
        ma3 = row['ma3']
        ema10 = row['ema10']
        kcu = row['kc_upper']
        print(f"{t} | C: {c:.6f} | MA3: {ma3:.6f} | EMA10: {ema10:.6f} | KCU: {kcu:.6f}")
except Exception as e:
    print(e)
