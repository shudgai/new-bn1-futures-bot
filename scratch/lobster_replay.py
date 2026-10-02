import json, urllib.request

url = "http://127.0.0.1:8006/api/klines?symbol=%E9%BE%99%E8%99%BE%2FUSDT&timeframe=1m&limit=100&include_live=true"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode())
    
klines = data['data']
start_ms = 1790910000

print(f"{'Time':>6} | {'Close':>7} | {'MA5':>7} | {'MA15':>7} | {'MA5_slp':>7} | {'MA15_slp':>8} | {'KC_mid':>7} | {'SWING_STATE':>12} | {'Old Exit':>25} | {'New Exit':>8}")

state = 'HOLD'
entry_price = 0.0520148

for i, k in enumerate(klines):
    if k['time'] < start_ms:
        continue
    
    t_str = f"11:{int((k['time'] - 1790906400)/60)%60:02d}"
    close = k['close']
    ma5 = k['ma5']
    ma15 = k['ma15']
    kc_mid = k['kc_middle']
    
    # We might need previous MAs from the API
    prev_k = klines[i-1] if i > 0 else k
    prev_ma5 = prev_k.get('ma5')
    prev_ma15 = prev_k.get('ma15')
    
    ma5_slope = (ma5 - prev_ma5) if (ma5 and prev_ma5) else 0.0
    ma15_slope = (ma15 - prev_ma15) if (ma15 and prev_ma15) else 0.0
    
    if ma5 is None: ma5 = 0
    if ma15 is None: ma15 = 0
    if kc_mid is None: kc_mid = 0

    # Old Exit: Peak Trailing at 11:01
    old_exit = "EXIT_REALTIME_PEAK_TRAILING" if k['time'] == 1790910060 else ""
    
    # SWING_HOLD Logic
    new_exit_allowed = "NO"
    
    if state == 'HOLD':
        if close >= kc_mid:
            state = 'WARNING'
    elif state == 'WARNING':
        if close >= kc_mid:
            # 2 consecutive closes above kc_mid
            if ma5_slope >= 0:
                state = 'RELEASED'
        else:
            state = 'HOLD'
            
    if ma5 >= ma15 and ma15 > 0:
        state = 'RELEASED'
        
    if state == 'RELEASED':
        new_exit_allowed = "YES"
        
    print(f"{t_str:>6} | {close:7.5f} | {ma5:7.5f} | {ma15:7.5f} | {ma5_slope:7.5f} | {ma15_slope:8.5f} | {kc_mid:7.5f} | {state:>12} | {old_exit:>25} | {new_exit_allowed:>8}")

