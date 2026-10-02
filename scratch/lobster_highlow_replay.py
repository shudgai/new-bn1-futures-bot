import json, urllib.request

url = "http://127.0.0.1:8006/api/klines?symbol=%E9%BE%99%E8%99%BE%2FUSDT&timeframe=1m&limit=100&include_live=true"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode())
    
klines = data['data']
start_ms = 1790910000

entry_price = 0.0520148
initial_stop = 0.05543
qty = 8619.989
initial_risk_price = initial_stop - entry_price
initial_R = initial_risk_price * qty
entry_atr = 0.002277

mfe_price = entry_price
mae_price = entry_price

def calc_r(price):
    return (entry_price - price) / initial_risk_price

print(f"entry_price: {entry_price}")
print(f"initial_stop: {initial_stop}")
print(f"initial_R: {initial_R:.2f} USDT")

print("\n--- 11:03 ~ 11:06 OHLC ---")
for k in klines:
    if k['time'] < start_ms: continue
    t_str = f"11:{int((k['time'] - 1790906400)/60)%60:02d}"
    if "11:03" <= t_str <= "11:06":
        print(f"{t_str} | O:{k['open']:.5f} H:{k['high']:.5f} L:{k['low']:.5f} C:{k['close']:.5f}")

for k in klines:
    if k['time'] < start_ms: continue
    if k['low'] < mfe_price: mfe_price = k['low']
    if k['high'] > mae_price: mae_price = k['high']
    
print(f"\nPeak MFE: {mfe_price} ({calc_r(mfe_price):.2f}R)")
print(f"Peak MAE: {mae_price} ({calc_r(mae_price):.2f}R)")

floors_to_test = [
    {"name": "1R -> Breakeven", "arm_r": 1.0, "floor_r": 0.0},
    {"name": "0.5 ATR -> Breakeven", "arm_atr": 0.5, "floor_atr": 0.0},
    {"name": "0.4 ATR -> 0.2 ATR", "arm_atr": 0.4, "floor_atr": 0.2},
]

for conf in floors_to_test:
    print(f"\n--- Tracing Floor: {conf['name']} ---")
    floor_price = None
    floor_armed_at = None
    hit_at = None
    hit_price = None
    
    for k in klines:
        if k['time'] < start_ms: continue
        t_str = f"11:{int((k['time'] - 1790906400)/60)%60:02d}"
        
        # 1. Check if we hit SL
        if k['high'] >= initial_stop:
            hit_at = t_str
            hit_price = initial_stop
            print(f"  {t_str} -> Hit INITIAL STOP at {initial_stop}")
            break
            
        # 2. Check if we hit floor
        if floor_price is not None and k['high'] >= floor_price:
            # We hit floor this bar. BUT wait, did we hit it before or after arming?
            # If armed in previous bar, we just hit it.
            hit_at = t_str
            hit_price = floor_price
            print(f"  {t_str} -> Hit FLOOR at {floor_price}")
            break
            
        # 3. Check if we arm floor this bar
        dist_r = calc_r(k['low'])
        dist_atr = (entry_price - k['low']) / entry_atr
        
        armed_this_bar = False
        if floor_price is None:
            if 'arm_r' in conf and dist_r >= conf['arm_r']:
                floor_price = entry_price - (conf['floor_r'] * initial_risk_price)
                armed_this_bar = True
            elif 'arm_atr' in conf and dist_atr >= conf['arm_atr']:
                floor_price = entry_price - (conf['floor_atr'] * entry_atr)
                armed_this_bar = True
                
        if armed_this_bar:
            print(f"  {t_str} -> Floor ARMED at {floor_price}")
            # If armed this bar, could it also hit THIS bar?
            if k['high'] >= floor_price:
                print(f"  {t_str} -> INTRABAR_ORDER_UNKNOWN: Armed and hit in the same bar! (Low={k['low']}, High={k['high']}, Floor={floor_price})")
                hit_at = f"{t_str} (Intra-bar)"
                hit_price = floor_price
                break

    if hit_at is None:
        print("  -> Floor NEVER HIT! Kept holding until end of replay.")

