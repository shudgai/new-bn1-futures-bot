import ccxt
import pandas as pd
import sys
import numpy as np

exchange = ccxt.binance()
klines = exchange.fetch_ohlcv('PEPE/USDT', '5m', limit=100)
df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
for col in ['open', 'high', 'low', 'close', 'volume']:
    df[col] = df[col].astype(float)

def _confirm(df, trough_lookback=10):
    reasons = []
    score = 0
    curr = df.iloc[-1]
    vol_curr = float(curr.get("volume", 0) or 0)
    rsi_curr = float(curr.get("rsi", 50) or 50)
    low_curr = float(curr.get("low", 0) or 0)
    window = df.iloc[-(trough_lookback + 1):-1]
    vol_ma = float(window["volume"].mean()) if len(window) > 0 else 0
    vol_shrunk = vol_curr <= vol_ma * 0.85
    if len(df) >= 3:
        vol_trough = float(df.iloc[-2].get("volume", 0))
        vol_after = float(df.iloc[-1].get("volume", 0))
        vol_expand = vol_after >= vol_trough * 1.2
    else:
        vol_expand = False
    
    if vol_shrunk or vol_expand:
        score += 40
        reasons.append(f"vol_shrunk={vol_shrunk}, vol_expand={vol_expand}")
        
    if "rsi" in df.columns and len(df) >= trough_lookback + 2:
        lookback_slice = df.iloc[-(trough_lookback + 1):-1]
        prev_low_idx = lookback_slice["low"].idxmin()
        prev_low_price = float(df.loc[prev_low_idx, "low"])
        prev_low_rsi = float(df.loc[prev_low_idx, "rsi"])
        if low_curr <= prev_low_price * 1.005 and rsi_curr > prev_low_rsi + 1.0:
            score += 40
            reasons.append(f"rsi_diverge, rsi={rsi_curr:.1f}, prev={prev_low_rsi:.1f}")
            
    return {"confirmed": score >= 40, "score": score, "reasons": reasons}

for i in range(len(df)-20, len(df)):
    sub_df = df.iloc[:i+1].copy()
    sub_df['ma5'] = sub_df['close'].rolling(5).mean()
    sub_df['ma25'] = sub_df['close'].rolling(25).mean()
    
    delta = sub_df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    sub_df['rsi'] = 100 - (100 / (1 + rs))

    ma5_curr = float(sub_df['ma5'].iloc[-1])
    ma5_prev = float(sub_df['ma5'].iloc[-2])
    ma5_prev2 = float(sub_df['ma5'].iloc[-3])
    
    is_trough = (ma5_curr > ma5_prev) and (ma5_prev < ma5_prev2)
    print(f"Index {i}, Close: {sub_df['close'].iloc[-1]}, is_trough: {is_trough}, MA5: {ma5_prev2:.6g} -> {ma5_prev:.6g} -> {ma5_curr:.6g}")
    if is_trough:
        print(f"  Confirming: {_confirm(sub_df)}")

