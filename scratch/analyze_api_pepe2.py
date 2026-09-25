import requests
import pandas as pd
from core.services.strategies.three_patterns import compute_pattern_indicators
import urllib.parse

symbol = "1000PEPE/USDT"
res = requests.get(f"http://127.0.0.1:8006/api/klines?symbol={urllib.parse.quote(symbol)}&timeframe=1m&limit=200&include_live=true")
klines = res.json()
if 'data' in klines:
    klines = klines['data']
df = pd.DataFrame(klines, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
df = compute_pattern_indicators(df)

df_target = df[(df.time >= 1790237100000) & (df.time <= 1790237580000)]

for i in range(len(df_target)):
    idx = df_target.index[i]
    row = df.iloc[idx]
    atr = row.atr
    body = row.close - row.open
    body_atr = abs(body) / atr if atr > 0 else 0
    kc_upper = row.kc_upper
    kc_lower = row.kc_lower
    ma15 = row.ma15
    baseline_vol = float(df.iloc[idx-20:idx]['volume'].astype(float).mean())
    vol_ratio = row.volume / baseline_vol if baseline_vol > 0 else 0
    print(f"Bar {row.time}: Open={row.open:.7f} Close={row.close:.7f} ATR={atr:.7f} Body={body:.7f} ({body_atr:.2f} ATR) VolRatio={vol_ratio:.2f}")
