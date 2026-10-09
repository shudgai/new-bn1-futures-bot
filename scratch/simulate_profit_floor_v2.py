import json
import pandas as pd
import numpy as np

def get_floor_d(peak):
    if peak < 3.0: return 0.0
    if peak <= 4.0: return 2.40 + (peak - 3.0) * ((3.40 - 2.40) / 1.0)
    if peak <= 5.0: return 3.40 + (peak - 4.0) * ((4.30 - 3.40) / 1.0)
    if peak < 6.0: return 4.30 + (peak - 5.0) * ((4.80 - 4.30) / 1.0)
    return peak * 0.80

def get_floor_e(peak):
    if peak < 3.0: return 0.0
    if peak <= 4.0: return 2.50 + (peak - 3.0) * ((3.50 - 2.50) / 1.0)
    if peak <= 5.0: return 3.50 + (peak - 4.0) * ((4.50 - 3.50) / 1.0)
    if peak < 6.0: return 4.50 + (peak - 5.0) * ((5.10 - 4.50) / 1.0)
    return peak * 0.85

def get_floor_a(peak):
    if peak >= 3.0: return peak - 1.0
    return 0.0

def calc_net(entry, ex_price, sign, qty, fee_rate=0.0005):
    if ex_price is None: return "NOT_HIT"
    gross = (ex_price - entry) * sign * qty
    fees = (entry * qty * fee_rate) + (ex_price * qty * fee_rate)
    return gross - fees

def simulate():
    with open('data/paper_account.json', 'r') as f:
        state = json.load(f)

    tid = 1790968085827
    t_open = next((t for t in state.get('trades', []) if t['id'] == tid), None)
    
    try:
        df_full = pd.read_csv('scratch/lobster_real_1m_history.csv')
    except:
        df_full = None
        
    if df_full is not None and t_open:
        df = df_full[df_full['timestamp'] >= t_open['id']].sort_values('timestamp').reset_index(drop=True)
        
        entry = t_open['price']
        atr = t_open['entry_atr']
        sign = -1
        qty = t_open['qty']
        
        floor_a = 0.0
        floor_d = 0.0
        floor_e = 0.0
        peak_gain_atr = 0.0
        
        exit_a = exit_d = exit_e = None
        time_a = time_d = time_e = None
        
        first_3atr_time = None
        
        intrabar_unknown = False
        
        for i, row in df.iterrows():
            ts = row['timestamp']
            h, l, c = row['high'], row['low'], row['close']
            bar_peak = l
            bar_valley = h
            
            bar_peak_atr = (bar_peak - entry) * sign / atr
            bar_valley_atr = (bar_valley - entry) * sign / atr
            
            # Record 3ATR first time
            if bar_peak_atr >= 3.0 and not first_3atr_time:
                first_3atr_time = ts
                
            if bar_peak_atr > peak_gain_atr:
                peak_gain_atr = bar_peak_atr
                
            new_floor_a = get_floor_a(peak_gain_atr)
            new_floor_d = get_floor_d(peak_gain_atr)
            new_floor_e = get_floor_e(peak_gain_atr)
            
            floor_a = max(floor_a, new_floor_a)
            floor_d = max(floor_d, new_floor_d)
            floor_e = max(floor_e, new_floor_e)
            
            # Check if hit floor within SAME bar as setting a new peak > floor
            # If bar_valley_atr <= floor, we hit the floor
            if not exit_a and floor_a > 0 and bar_valley_atr <= floor_a:
                exit_a = entry + sign * (floor_a * atr)
                time_a = ts
                if bar_peak_atr > floor_a: intrabar_unknown = True
            if not exit_d and floor_d > 0 and bar_valley_atr <= floor_d:
                exit_d = entry + sign * (floor_d * atr)
                time_d = ts
                if bar_peak_atr > floor_d: intrabar_unknown = True
            if not exit_e and floor_e > 0 and bar_valley_atr <= floor_e:
                exit_e = entry + sign * (floor_e * atr)
                time_e = ts
                if bar_peak_atr > floor_e: intrabar_unknown = True
                
        print(f"LOBSTER_PEAK_ATR = {peak_gain_atr}")
        print(f"LOBSTER_FIRST_3ATR_TIME = {first_3atr_time}")
        print(f"LOBSTER_A_NET = {calc_net(entry, exit_a, sign, qty)}")
        print(f"LOBSTER_D_NET = {calc_net(entry, exit_d, sign, qty)}")
        print(f"LOBSTER_E_NET = {calc_net(entry, exit_e, sign, qty)}")
        print(f"A_GIVEBACK = {peak_gain_atr - (exit_a-entry)*sign/atr if exit_a else 0}")
        print(f"D_GIVEBACK = {peak_gain_atr - (exit_d-entry)*sign/atr if exit_d else 0}")
        print(f"E_GIVEBACK = {peak_gain_atr - (exit_e-entry)*sign/atr if exit_e else 0}")
        print(f"INTRABAR_UNKNOWN = {intrabar_unknown}")

simulate()
