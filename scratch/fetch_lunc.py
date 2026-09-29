import requests
url = "https://fapi.binance.com/fapi/v1/klines"
params = {"symbol": "1000LUNCUSDT", "interval": "1m", "startTime": 1790388840000, "endTime": 1790389020000}
r = requests.get(url, params=params)
data = r.json()
for d in data:
    print(f"Time: {d[0]}, Open: {d[1]}, High: {d[2]}, Low: {d[3]}, Close: {d[4]}")
