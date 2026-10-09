import requests

ts = 1791171221679
start = ts - 120000

for sym in ['1000LUNCUSDT', 'NEIROUSDT', '1000PEPEUSDT']:
    url = 'https://fapi.binance.com/fapi/v1/klines'
    params = {'symbol': sym, 'interval': '1m', 'startTime': start, 'limit': 3}
    r = requests.get(url, params=params)
    if r.status_code == 200:
        klines = r.json()
        print(f"SYM: {sym}")
        for k in klines:
            print(f"  {k[0]} O:{k[1]} H:{k[2]} L:{k[3]} C:{k[4]}")

