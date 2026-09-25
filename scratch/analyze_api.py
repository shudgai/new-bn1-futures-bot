import requests
import pandas as pd
from core.services.strategies.three_patterns import compute_pattern_indicators
import urllib.parse

for symbol in ["1000PEPE/USDT", "龙虾/USDT"]:
    res = requests.get(f"http://127.0.0.1:8006/api/klines?symbol={urllib.parse.quote(symbol)}&timeframe=1m&limit=200&include_live=true")
    if res.status_code != 200:
        continue
    klines = res.json()
    if 'data' in klines:
        klines = klines['data']
    df = pd.DataFrame(klines)
    df = compute_pattern_indicators(df)
    
    # filter 12:00 to 12:20 (04:00 to 04:20 UTC)
    df_target = df[(df.time >= 1790222400) & (df.time <= 1790223600)]
    
    print(f"\n=== {symbol} ===")
    for i in range(len(df_target)):
        idx = df_target.index[i]
        row = df.iloc[idx]
        
        atr = row.atr
        body = row.close - row.open
        body_atr = abs(body) / atr if atr > 0 else 0
        
        baseline = df.iloc[idx-21:idx-1]['volume'].astype(float).mean() if idx >= 21 and 'volume' in df.columns else 1
        vol = row.volume if 'volume' in row else 0
        vol_ratio = vol / baseline if baseline > 0 else 0
        
        kc_upper = row.kc_upper
        kc_lower = row.kc_lower
        
        dist_out = 0
        if body > 0:
            dist_out = row.close - kc_upper
            dist_out_str = f"{dist_out:.6f} above Upper" if dist_out > 0 else f"{dist_out:.6f} below Upper"
        else:
            dist_out = kc_lower - row.close
            dist_out_str = f"{dist_out:.6f} below Lower" if dist_out > 0 else f"{dist_out:.6f} above Lower"
            
        print(f"Bar {row.time}: Close={row.close:.6f}, ATR={atr:.6f}, VolRatio={vol_ratio:.2f}, Body={body:.6f} ({body_atr:.2f} ATR), {dist_out_str}")

