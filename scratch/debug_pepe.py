import json
import pandas as pd
import requests
import time

def fetch_klines(symbol):
    url = f"http://127.0.0.1:8006/api/klines?symbol={symbol}&timeframe=1m&limit=10&include_live=true"
    resp = requests.get(url)
    if resp.status_code == 200:
        return resp.json()
    return None

data = fetch_klines("1000PEPE/USDT")
if not data:
    print("No data")
    exit(1)

df = pd.DataFrame(data)
if len(df) < 4:
    print("Not enough data")
    exit(1)

k0 = df.iloc[-4]
k1 = df.iloc[-3]
k2 = df.iloc[-2]
k3 = df.iloc[-1]

print(f"k0: O={k0['open']}, C={k0['close']}, kc_upper={k0.get('kc_upper',0)}")
print(f"k1: O={k1['open']}, C={k1['close']}, kc_upper={k1.get('kc_upper',0)}")
print(f"k2: O={k2['open']}, C={k2['close']}, kc_upper={k2.get('kc_upper',0)}, atr={k2.get('atr',0)}, H={k2['high']}, L={k2['low']}")
print(f"k3: O={k3['open']}, C={k3['close']}")

# Gate 1 & 2: Position & Shape
is_initial_breakout = (k0['close'] <= k0.get('kc_upper', 0) and k1['close'] > k1.get('kc_upper', 0))
is_continuation = (k0['close'] > k0.get('kc_upper', 0) and k1['close'] > k1.get('kc_upper', 0))

print(f"Gate 1: is_initial_breakout={is_initial_breakout}, is_continuation={is_continuation}")
if not (is_initial_breakout or is_continuation):
    print("FAILED AT GATE 1")

k2_body = k2['close'] - k2['open']
k2_range = k2['high'] - k2['low']
k2_upper_wick = k2['high'] - max(k2['open'], k2['close'])
k2_atr = k2.get('atr', k1.get('atr', 0))

print(f"Gate 2 checks: k2_body={k2_body}, 0.4*atr={0.4*k2_atr}, wick/range={k2_upper_wick/k2_range if k2_range>0 else 0}")
if k2['close'] < k2['open']:
    print("FAILED AT GATE 2: k2 is red")
if k2['close'] <= k2.get('kc_upper', 0):
    print("FAILED AT GATE 2: k2 below kc_upper")
if k2_body < 0.4 * k2_atr:
    print("FAILED AT GATE 2: k2 body < 0.4 ATR")
if k2_range > 0 and (k2_upper_wick / k2_range) > 0.35:
    print("FAILED AT GATE 2: k2 upper wick > 35%")

