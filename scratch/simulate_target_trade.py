import json
import pandas as pd

def get_floor_d(peak):
    if peak < 3.0: return 0.0
    if peak <= 4.0: return 2.40 + (peak - 3.0) * ((3.40 - 2.40) / 1.0)
    if peak <= 5.0: return 3.40 + (peak - 4.0) * ((4.30 - 3.40) / 1.0)
    if peak < 6.0: return 4.30 + (peak - 5.0) * ((4.80 - 4.30) / 1.0)
    return max(4.80, peak * 0.80)

def get_floor_e(peak):
    if peak < 3.0: return 0.0
    if peak <= 4.0: return 2.50 + (peak - 3.0) * ((3.50 - 2.50) / 1.0)
    if peak <= 5.0: return 3.50 + (peak - 4.0) * ((4.50 - 3.50) / 1.0)
    if peak < 6.0: return 4.50 + (peak - 5.0) * ((5.10 - 4.50) / 1.0)
    return max(5.10, peak * 0.85)

def get_floor_a(peak):
    if peak >= 3.0: return peak - 1.0
    return 0.0

with open('data/paper_account.json', 'r') as f:
    state = json.load(f)

trade_id = 1790986989938
t_open = next((t for t in state.get('trades', []) if t['id'] == trade_id), None)
cbid = t_open.get('channel_confirmation_bar_id')
t_close = next((t for t in state.get('trades', []) if t.get('action') == 'CLOSE_SHORT' and t.get('symbol') == '龙虾/USDT' and t.get('channel_confirmation_bar_id') == cbid and t['id'] > t_open['id']), None)

t1 = t_open['id']
t2 = t_close['id']
entry = t_open['price']
atr = t_open['entry_atr']
qty = t_open['qty']
sign = -1

print(f"OPEN: {t1}, CLOSE: {t2}, ENTRY: {entry}, ATR: {atr}, EXIT: {t_close['price']}, PNL: {t_close['pnl']}")

df_full = pd.read_csv('scratch/lobster_real_1m_history.csv')

# Find candles that overlap with the open window [t1, t2]
# The timestamp in CSV is the candle start time (ms). A 1M candle ends at timestamp + 60000.
# A candle overlaps if timestamp < t2 and timestamp + 60000 > t1
df = df_full[(df_full['timestamp'] < t2) & (df_full['timestamp'] + 60000 > t1)].copy()

# Categorize candles
def categorize_candle(ts):
    end_ts = ts + 60000
    if ts >= t1 and end_ts <= t2:
        return 'FULLY_HELD_CANDLES'
    elif ts < t1:
        return 'PARTIAL_ENTRY_CANDLE'
    elif end_ts > t2:
        return 'PARTIAL_EXIT_CANDLE'
    return 'UNKNOWN'

df['category'] = df['timestamp'].apply(categorize_candle)

max_full_peak_atr = 0.0
max_full_net = 0.0
first_1atr = first_2atr = first_3atr = first_4atr = first_5atr = None

for i, row in df[df['category'] == 'FULLY_HELD_CANDLES'].iterrows():
    ts = row['timestamp']
    l = row['low']
    peak_atr = (l - entry) * sign / atr
    if peak_atr > max_full_peak_atr:
        max_full_peak_atr = peak_atr
        net = peak_atr * atr * qty - (entry * qty * 0.0005 * 2)
        max_full_net = net
    
    if first_1atr is None and peak_atr >= 1.0: first_1atr = ts
    if first_2atr is None and peak_atr >= 2.0: first_2atr = ts
    if first_3atr is None and peak_atr >= 3.0: first_3atr = ts
    if first_4atr is None and peak_atr >= 4.0: first_4atr = ts
    if first_5atr is None and peak_atr >= 5.0: first_5atr = ts

print(f"MAX_FULL_CANDLE_MFE_ATR: {max_full_peak_atr}")
print(f"MAX_FULL_CANDLE_THEORETICAL_NET: {max_full_net}")
print(f"FIRST_1_ATR_CANDLE: {first_1atr}")
print(f"FIRST_3_ATR_CANDLE: {first_3atr}")
print(f"FIRST_5_ATR_CANDLE: {first_5atr}")

actual_exit_atr = (t_close['price'] - entry) * sign / atr
print(f"ACTUAL_EXIT_PROFIT_ATR: {actual_exit_atr}")
print(f"OBSERVED_CANDLE_GIVEBACK_ATR: {max_full_peak_atr - actual_exit_atr}")
print(f"THEORETICAL_NET_GIVEBACK_USDT: {max_full_net - t_close['pnl']}")

# Simulate Floors A, D, E
floor_a = floor_d = floor_e = 0.0
peak_gain_atr = 0.0

exit_a_scenarios = []
exit_d_scenarios = []
exit_e_scenarios = []

intrabar_unknown = False

for i, row in df.iterrows():
    ts = row['timestamp']
    h, l = row['high'], row['low']
    
    bar_peak_atr = (l - entry) * sign / atr
    bar_valley_atr = (h - entry) * sign / atr
    
    prev_peak = peak_gain_atr
    if bar_peak_atr > peak_gain_atr:
        peak_gain_atr = bar_peak_atr
        
    new_a = get_floor_a(peak_gain_atr)
    new_d = get_floor_d(peak_gain_atr)
    new_e = get_floor_e(peak_gain_atr)
    
    floor_a = max(floor_a, new_a)
    floor_d = max(floor_d, new_d)
    floor_e = max(floor_e, new_e)
    
    if len(exit_a_scenarios) == 0 and floor_a > 0:
        if bar_valley_atr <= floor_a:
            # We hit floor_a on this bar
            if bar_peak_atr > prev_peak and bar_peak_atr >= 3.0:
                # Ambiguous! We made a new peak AND hit floor in same bar
                intrabar_unknown = True
                exit_a_scenarios = [floor_a, bar_valley_atr] # worst, best
            else:
                exit_a_scenarios = [floor_a, floor_a]
                
    if len(exit_d_scenarios) == 0 and floor_d > 0:
        if bar_valley_atr <= floor_d:
            if bar_peak_atr > prev_peak and bar_peak_atr >= 3.0:
                intrabar_unknown = True
                exit_d_scenarios = [floor_d, bar_valley_atr]
            else:
                exit_d_scenarios = [floor_d, floor_d]

    if len(exit_e_scenarios) == 0 and floor_e > 0:
        if bar_valley_atr <= floor_e:
            if bar_peak_atr > prev_peak and bar_peak_atr >= 3.0:
                intrabar_unknown = True
                exit_e_scenarios = [floor_e, bar_valley_atr]
            else:
                exit_e_scenarios = [floor_e, floor_e]

def calc_net(ex_atr):
    if not ex_atr: return "NOT_HIT"
    ex_p = entry + sign * (ex_atr * atr)
    gross = (ex_p - entry) * sign * qty
    fees = (entry * qty * 0.0005) + (ex_p * qty * 0.0005)
    return gross - fees

if exit_a_scenarios: print(f"A_EST_NET_RANGE: {[calc_net(x) for x in exit_a_scenarios]}")
else: print("A_EST_NET_RANGE: NOT_HIT")
if exit_d_scenarios: print(f"D_EST_NET_RANGE: {[calc_net(x) for x in exit_d_scenarios]}")
else: print("D_EST_NET_RANGE: NOT_HIT")
if exit_e_scenarios: print(f"E_EST_NET_RANGE: {[calc_net(x) for x in exit_e_scenarios]}")
else: print("E_EST_NET_RANGE: NOT_HIT")

print(f"INTRABAR_ORDER_UNKNOWN: {intrabar_unknown}")
