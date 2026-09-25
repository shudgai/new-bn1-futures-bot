import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.services.strategies.unified_entry_strategy import _check_long_pipeline
import requests
import pandas as pd

def fetch_klines(symbol):
    url = f"http://127.0.0.1:8006/api/klines?symbol={symbol}&timeframe=1m&limit=100&include_live=true"
    resp = requests.get(url)
    return resp.json().get("data", [])

data = fetch_klines("1000PEPE/USDT")
if not data:
    exit(1)
df = pd.DataFrame(data)

k0 = df.iloc[-4]
k1 = df.iloc[-3]
k2 = df.iloc[-2]
k3 = df.iloc[-1]

print(f"ATR from API: {k2.get('atr', 'MISSING')}")

# Manually print the pipeline steps
k2_atr = k2.get('atr', k1.get('atr', 0))
if pd.isna(k2_atr) or k2_atr is None: k2_atr = 0

print(f"Gate 1 evaluation:")
is_initial_breakout = (k0['close'] <= k0.get('kc_upper', 0) and k1['close'] > k1.get('kc_upper', 0))
is_continuation = (k0['close'] > k0.get('kc_upper', 0) and k1['close'] > k1.get('kc_upper', 0))
print(f"  is_initial: {is_initial_breakout}, is_continuation: {is_continuation}")

k2_body = k2['close'] - k2['open']
k2_range = k2['high'] - k2['low']
k2_upper_wick = k2['high'] - max(k2['open'], k2['close'])

print(f"Gate 2 evaluation:")
print(f"  k2 is red: {k2['close'] < k2['open']}")
print(f"  k2 below upper: {k2['close'] <= k2.get('kc_upper', 0)}")
print(f"  k2 body ({k2_body}) < 0.4*ATR ({0.4*k2_atr}): {k2_body < 0.4 * k2_atr}")
print(f"  k2 wick > 35%: {k2_range > 0 and (k2_upper_wick / k2_range) > 0.35}")
