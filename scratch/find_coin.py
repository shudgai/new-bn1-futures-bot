import requests

ts = 1791171221679
start = ts - 60000

url = 'https://fapi.binance.com/fapi/v1/ticker/price'
r = requests.get(url)
if r.status_code == 200:
    for item in r.json():
        if 0.04 < float(item['price']) < 0.06:
            print(item['symbol'])
