import json
import pandas as pd
import numpy as np

def get_floor_b(peak):
    if peak < 1.5: return 0.0
    if peak <= 2.0: return 0.75 + (peak - 1.5) * ((1.30 - 0.75) / 0.5)
    if peak <= 3.0: return 1.30 + (peak - 2.0) * ((2.40 - 1.30) / 1.0)
    if peak <= 4.0: return 2.40 + (peak - 3.0) * ((3.40 - 2.40) / 1.0)
    if peak <= 5.0: return 3.40 + (peak - 4.0) * ((4.30 - 3.40) / 1.0)
    if peak < 6.0: return 4.30 + (peak - 5.0) * ((4.80 - 4.30) / 1.0) # approx 6*0.8=4.8
    return peak * 0.80

def get_floor_c(peak):
    if peak < 1.5: return 0.0
    if peak <= 2.0: return 0.90 + (peak - 1.5) * ((1.50 - 0.90) / 0.5)
    if peak <= 3.0: return 1.50 + (peak - 2.0) * ((2.50 - 1.50) / 1.0)
    if peak <= 4.0: return 2.50 + (peak - 3.0) * ((3.50 - 2.50) / 1.0)
    if peak <= 5.0: return 3.50 + (peak - 4.0) * ((4.50 - 3.50) / 1.0)
    if peak < 6.0: return 4.50 + (peak - 5.0) * ((5.10 - 4.50) / 1.0) # approx 6*0.85=5.1
    return peak * 0.85

def get_floor_a(peak):
    if peak >= 3.0: return peak - 1.0
    return 0.0

def simulate():
    with open('data/paper_account.json', 'r') as f:
        state = json.load(f)

    # Trades to analyze
    target_ids = [1790968085827, 1791080487139]
    trades = [t for t in state.get('trades', []) if t['action'].startswith('OPEN')]
    
    # Also find some winning trades > 1.5 ATR peak
    for t in state.get('trades', []):
        if t['action'].startswith('CLOSE') and t['pnl'] > 0:
            pass # We don't easily have peak ATR in close trades unless we check MFE.

    # Let's just output the curve characteristics for 3 ATR
    print(f"CURVE_B_3ATR_GIVEBACK = {3.0 - get_floor_b(3.0):.2f}")
    print(f"CURVE_B_3ATR_RETAINED = {get_floor_b(3.0):.2f}")
    print(f"CURVE_C_3ATR_GIVEBACK = {3.0 - get_floor_c(3.0):.2f}")
    print(f"CURVE_C_3ATR_RETAINED = {get_floor_c(3.0):.2f}")

    # Let's simulate for the specific targets using dummy intrabar
    # Without full 1m data for the second trade, we'll just simulate 1790968085827 exactly using our csv.
    try:
        df_full = pd.read_csv('scratch/lobster_real_1m_history.csv')
    except:
        df_full = None
        
    for tid in target_ids:
        t_open = next((t for t in state.get('trades', []) if t['id'] == tid), None)
        t_close = next((t for t in state.get('trades', []) if t.get('pair_id') == tid and t['action'].startswith('CLOSE')), None)
        
        if not t_open:
            print(f"Trade {tid} NOT FOUND")
            continue
            
        print(f"\n--- TRADE {tid} ---")
        print(f"Actual Net: {t_close['pnl'] if t_close else 'OPEN'}")
        
        # If we have df_full, we simulate
        if df_full is not None and t_open['symbol'] == '龙虾/USDT':
            df = df_full[df_full['timestamp'] >= t_open['id']].sort_values('timestamp').reset_index(drop=True)
            if len(df) == 0:
                print("No 1m data for this trade.")
                continue
            
            entry = t_open['price']
            atr = t_open['entry_atr']
            sign = 1 if t_open['side'] == 'LONG' else -1
            qty = t_open['qty']
            fee_rate = 0.0005
            
            floor_a = 0.0
            floor_b = 0.0
            floor_c = 0.0
            peak_gain_atr = 0.0
            
            exit_a = exit_b = exit_c = None
            
            for i, row in df.iterrows():
                # rough simulation
                h, l, c = row['high'], row['low'], row['close']
                if sign == 1:
                    bar_peak = h
                    bar_valley = l
                else:
                    bar_peak = l
                    bar_valley = h
                    
                bar_peak_atr = (bar_peak - entry) * sign / atr
                bar_valley_atr = (bar_valley - entry) * sign / atr
                
                # update peak
                if bar_peak_atr > peak_gain_atr:
                    peak_gain_atr = bar_peak_atr
                    
                # update floors
                floor_a = max(floor_a, get_floor_a(peak_gain_atr))
                floor_b = max(floor_b, get_floor_b(peak_gain_atr))
                floor_c = max(floor_c, get_floor_c(peak_gain_atr))
                
                # check if floor hit (intrabar approximation)
                if not exit_a and floor_a > 0 and bar_valley_atr <= floor_a:
                    exit_a = entry + sign * (floor_a * atr)
                if not exit_b and floor_b > 0 and bar_valley_atr <= floor_b:
                    exit_b = entry + sign * (floor_b * atr)
                if not exit_c and floor_c > 0 and bar_valley_atr <= floor_c:
                    exit_c = entry + sign * (floor_c * atr)
                    
                if exit_a and exit_b and exit_c:
                    break
                    
            def calc_net(ex_price):
                if ex_price is None: return "NOT_HIT"
                gross = (ex_price - entry) * sign * qty
                fees = (entry * qty * fee_rate) + (ex_price * qty * fee_rate)
                return gross - fees
                
            print(f"A_NET_PNL: {calc_net(exit_a)}")
            print(f"B_NET_PNL: {calc_net(exit_b)}")
            print(f"C_NET_PNL: {calc_net(exit_c)}")
            print("Status: APPROXIMATION (INTRABAR_ORDER_UNKNOWN)")
        else:
            print("No full data to replay.")

if __name__ == '__main__':
    simulate()
