import pandas as pd
import numpy as np
import sys, os
from datetime import datetime

# Load data
df = pd.read_csv("scratch/lobster_real_1m_history.csv")
df.sort_values('timestamp', inplace=True)
df.reset_index(drop=True, inplace=True)

# Compute indicators manually in a fast way
close = df['close'].values
high = df['high'].values
low = df['low'].values
open_p = df['open'].values
ts = df['timestamp'].values

N = len(df)
ma3 = np.full(N, np.nan)
ma5 = np.full(N, np.nan)
ma15 = np.full(N, np.nan)
ema20 = np.full(N, np.nan)
atr = np.full(N, np.nan)
kc_upper = np.full(N, np.nan)
kc_middle = np.full(N, np.nan)
kc_lower = np.full(N, np.nan)
direction = np.zeros(N) # 1 for UP, -1 for DOWN

# EMAs
alpha = 2.0 / (20 + 1)
ema20[0] = close[0]
for i in range(1, N):
    ema20[i] = close[i] * alpha + ema20[i-1] * (1 - alpha)

# ATR (assuming 10 period simple moving average of True Range, as is typical in Binance examples, or Wilder. Let's do SMA of TR)
tr = np.zeros(N)
for i in range(1, N):
    tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
for i in range(10, N):
    atr[i] = np.mean(tr[i-9:i+1])

kc_middle = ema20
for i in range(10, N):
    kc_upper[i] = kc_middle[i] + 2.0 * atr[i]
    kc_lower[i] = kc_middle[i] - 2.0 * atr[i]
    
for i in range(2, N):
    ma3[i] = np.mean(close[i-2:i+1])
for i in range(4, N):
    ma5[i] = np.mean(close[i-4:i+1])
for i in range(14, N):
    ma15[i] = np.mean(close[i-14:i+1])

# Direction (based on MA15 slope or KC middle slope)
# The slow pandas loop did `direction.iloc[i] = -1`. We will just assume KC direction = sign(kc_middle[i] - kc_middle[i-1])
for i in range(1, N):
    if kc_middle[i] > kc_middle[i-1]:
        direction[i] = 1
    elif kc_middle[i] < kc_middle[i-1]:
        direction[i] = -1
    else:
        direction[i] = direction[i-1]

# Now simulate models
model_A = []
model_B = []
model_C10 = []
model_C20 = []
model_C30 = []

for i in range(20, N - 5):
    # Base structural filters
    
    # K1 logic
    k1_ts = ts[i]
    k1_c = close[i]
    k1_o = open_p[i]
    k1_kc_l = kc_lower[i]
    k1_kc_u = kc_upper[i]
    k1_dir = direction[i]
    
    is_short_k1 = (k1_dir == -1) and (k1_c < k1_kc_l) and (k1_c < k1_o)
    is_long_k1 = (k1_dir == 1) and (k1_c > k1_kc_u) and (k1_c > k1_o)
    
    if not (is_short_k1 or is_long_k1):
        continue
        
    side = 'SHORT' if is_short_k1 else 'LONG'
    sign = -1 if is_short_k1 else 1
    k1_atr = atr[i]
    
    # Model B (Fast Entry) - entry happens at K1 close
    model_B.append({
        'side': side,
        'k1_ts': k1_ts,
        'entry_ts': k1_ts,
        'entry_price': k1_c,
        'k1_atr': k1_atr,
        'i': i
    })
    
    # Model C (Hybrid) - entry happens during K2
    k2_c = close[i+1]
    k2_o = open_p[i+1]
    k2_h = high[i+1]
    k2_l = low[i+1]
    k2_dir = direction[i+1]
    
    if k2_dir == k1_dir:
        # Simulate live forming. We just use K2 close to approximate the threshold crossover point
        # Or more accurately, check if K2 body ratio ever reaches threshold.
        # Since it's 1m, we use OHLC
        k2_span = max(k2_h, k2_o, k2_c) - min(k2_l, k2_o, k2_c)
        if k2_span > 0:
            k2_ratio = abs(k2_c - k2_o) / k2_span
            is_valid_dir = (k2_c < k2_o) if side == 'SHORT' else (k2_c > k2_o)
            
            if is_valid_dir:
                if k2_ratio > 0.10:
                    model_C10.append({'side': side, 'k1_ts': k1_ts, 'entry_ts': ts[i+1], 'entry_price': k2_c, 'k1_atr': k1_atr, 'i': i+1})
                if k2_ratio > 0.20:
                    model_C20.append({'side': side, 'k1_ts': k1_ts, 'entry_ts': ts[i+1], 'entry_price': k2_c, 'k1_atr': k1_atr, 'i': i+1})
                if k2_ratio > 0.30:
                    model_C30.append({'side': side, 'k1_ts': k1_ts, 'entry_ts': ts[i+1], 'entry_price': k2_c, 'k1_atr': k1_atr, 'i': i+1})
                    
    # Model A (Current) - entry during K3
    k2_kc_edge = kc_lower[i+1] if side == 'SHORT' else kc_upper[i+1]
    k2_outside = (k2_c < k2_kc_edge) if side == 'SHORT' else (k2_c > k2_kc_edge)
    is_k2_valid_dir = (k2_c < k2_o) if side == 'SHORT' else (k2_c > k2_o)
    k2_ma5_ma15_ok = (ma5[i+1] < ma15[i+1]) if side == 'SHORT' else (ma5[i+1] > ma15[i+1])
    
    if k2_outside and is_k2_valid_dir and k2_ma5_ma15_ok:
        k3_c = close[i+2]
        k3_o = open_p[i+2]
        k3_h = high[i+2]
        k3_l = low[i+2]
        k3_span = max(k3_h, k3_o, k3_c) - min(k3_l, k3_o, k3_c)
        if k3_span > 0:
            k3_ratio = abs(k3_c - k3_o) / k3_span
            k3_valid_dir = (k3_c < k3_o) if side == 'SHORT' else (k3_c > k3_o)
            if k3_valid_dir and k3_ratio > 0.10:
                model_A.append({'side': side, 'k1_ts': k1_ts, 'entry_ts': ts[i+2], 'entry_price': k3_c, 'k1_atr': k1_atr, 'i': i+2})


def evaluate(entries):
    for e in entries:
        idx = e['i']
        side = e['side']
        sign = -1 if side == 'SHORT' else 1
        edge_col = kc_lower if side == 'SHORT' else kc_upper
        
        # False break check: 3 bars
        fb3 = False
        for offset in range(1, 4):
            if side == 'SHORT':
                if close[idx + offset] > edge_col[idx + offset]: fb3 = True
            else:
                if close[idx + offset] < edge_col[idx + offset]: fb3 = True
        e['fb3'] = fb3
        
        e['delay_bars'] = idx - np.where(ts == e['k1_ts'])[0][0]
        e['delay_atr'] = abs(e['entry_price'] - close[idx - e['delay_bars']]) / e['k1_atr']

evaluate(model_A)
evaluate(model_B)
evaluate(model_C10)
evaluate(model_C20)
evaluate(model_C30)

print(f"DATA_RANGE = {ts[0]} to {ts[-1]}")
print(f"TOTAL_1M_BARS = {N}")
print(f"MODEL_A_ENTRIES = {len(model_A)}")
print(f"MODEL_B_ENTRIES = {len(model_B)}")
print(f"MODEL_C10_ENTRIES = {len(model_C10)}")
print(f"MODEL_C20_ENTRIES = {len(model_C20)}")
print(f"MODEL_C30_ENTRIES = {len(model_C30)}")

if model_A: print(f"A_FALSE_BREAK_3BAR = {sum(1 for e in model_A if e['fb3']) / len(model_A):.2f}")
if model_B: print(f"B_FALSE_BREAK_3BAR = {sum(1 for e in model_B if e['fb3']) / len(model_B):.2f}")
if model_C10: print(f"C10_FALSE_BREAK_3BAR = {sum(1 for e in model_C10 if e['fb3']) / len(model_C10):.2f}")
if model_C20: print(f"C20_FALSE_BREAK_3BAR = {sum(1 for e in model_C20 if e['fb3']) / len(model_C20):.2f}")
if model_C30: print(f"C30_FALSE_BREAK_3BAR = {sum(1 for e in model_C30 if e['fb3']) / len(model_C30):.2f}")

if model_A: print(f"A_MEDIAN_ENTRY_DELAY_ATR = {np.median([e['delay_atr'] for e in model_A]):.2f}")
if model_B: print(f"B_MEDIAN_ENTRY_DELAY_ATR = {np.median([e['delay_atr'] for e in model_B]):.2f}")
if model_C10: print(f"C10_MEDIAN_ENTRY_DELAY_ATR = {np.median([e['delay_atr'] for e in model_C10]):.2f}")
if model_C20: print(f"C20_MEDIAN_ENTRY_DELAY_ATR = {np.median([e['delay_atr'] for e in model_C20]):.2f}")
if model_C30: print(f"C30_MEDIAN_ENTRY_DELAY_ATR = {np.median([e['delay_atr'] for e in model_C30]):.2f}")

lob_ts = 1790965020000.0
for e in model_A: 
    if e['k1_ts'] == lob_ts: print(f"LOBSTER_A_DELAY_ATR = {e['delay_atr']:.2f}")
for e in model_B: 
    if e['k1_ts'] == lob_ts: print(f"LOBSTER_B_DELAY_ATR = {e['delay_atr']:.2f}")
for e in model_C10: 
    if e['k1_ts'] == lob_ts: print(f"LOBSTER_C10_DELAY_ATR = {e['delay_atr']:.2f}")
for e in model_C20: 
    if e['k1_ts'] == lob_ts: print(f"LOBSTER_C20_DELAY_ATR = {e['delay_atr']:.2f}")
for e in model_C30: 
    if e['k1_ts'] == lob_ts: print(f"LOBSTER_C30_DELAY_ATR = {e['delay_atr']:.2f}")
