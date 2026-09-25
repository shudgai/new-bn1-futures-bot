import requests
import pandas as pd
from core.services.strategies.three_patterns import compute_pattern_indicators
import urllib.parse

symbol = "龙虾/USDT"
res = requests.get(f"http://127.0.0.1:8006/api/klines?symbol={urllib.parse.quote(symbol)}&timeframe=1m&limit=200&include_live=true")
klines = res.json()
if 'data' in klines:
    klines = klines['data']
df = pd.DataFrame(klines)
df = compute_pattern_indicators(df)

df_target = df[(df.time >= 1790231220) & (df.time <= 1790231580)]

for i in range(len(df_target)):
    idx = df_target.index[i]
    row = df.iloc[idx]
    atr = row.atr
    body = row.close - row.open
    body_atr = abs(body) / atr if atr > 0 else 0
    kc_upper = row.kc_upper
    kc_lower = row.kc_lower
    ma15 = row.ma15
    print(f"Bar {row.time}: Open={row.open:.5f} Close={row.close:.5f} ATR={atr:.5f} Body={body:.5f} ({body_atr:.2f} ATR)")
