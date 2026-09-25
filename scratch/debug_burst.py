import pandas as pd
import requests

res = requests.get("http://127.0.0.1:8006/api/klines?symbol=%E9%BE%99%E8%99%BE%2FUSDT&timeframe=1m&limit=10&include_live=true")
df = pd.DataFrame(res.json()["data"])
price = float(df["close"].iloc[-1])

live_row = df.iloc[-1]
live_open = float(live_row['open'])
live_high = max(float(live_row['high']), price)
live_low = min(float(live_row['low']), price)
live_close = float(price)

live_kc_up = float(live_row.get('kc_upper', 0))
live_kc_dn = float(live_row.get('kc_lower', 0))
live_kc_mid = float(live_row.get('kc_middle', live_row.get('ema_20', 0)))
live_atr = float(live_row.get('atr', 0))

print(f"Price: {price}")
print(f"Open: {live_open}, Close: {live_close}")
print(f"KC_Mid: {live_kc_mid}, KC_Up: {live_kc_up}")
print(f"ATR: {live_atr}")

live_body = abs(live_close - live_open)
live_range = live_high - live_low
print(f"Body: {live_body}, Range: {live_range}")

if live_atr > 0:
    print(f"live_range / live_atr = {live_range / live_atr}")
    if live_range > 0:
        print(f"body / range = {live_body / live_range}")
    print(f"Threshold open (LONG): {live_kc_mid + 0.3 * live_atr}")
