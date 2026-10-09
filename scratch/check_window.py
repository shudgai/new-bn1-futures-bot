import json
import pandas as pd

with open('data/paper_account.json', 'r') as f:
    state = json.load(f)

trade_id = 1790968085827
t_open = None
cbid = None

for t in state.get('trades', []):
    if t.get('id') == trade_id and t.get('action').startswith('OPEN'):
        t_open = t
        cbid = t.get('channel_confirmation_bar_id')
        break

t_close = None
for t in state.get('trades', []):
    if t.get('action') == 'CLOSE_SHORT' and t.get('symbol') == '龙虾/USDT':
        if t.get('channel_confirmation_bar_id') == cbid:
            t_close = t
            break

if t_open and t_close:
    entry = t_open['price']
    atr = t_open['entry_atr']
    sign = -1
    t1 = t_open['id']
    t2 = t_close['id']
    
    print(f"OPEN_TIME = {t1}")
    print(f"CLOSE_TIME = {t2}")
    
    try:
        df_full = pd.read_csv('scratch/lobster_real_1m_history.csv')
        # Exact window (since df_full is 1m candles, its timestamp is the candle start. The trade open/close is in ms)
        df_window = df_full[(df_full['timestamp'] >= t1 - 60000) & (df_full['timestamp'] <= t2 + 60000)]
        
        peak_gain_atr = 0.0
        for i, row in df_window.iterrows():
            l = row['low']
            bar_peak_atr = (l - entry) * sign / atr
            if bar_peak_atr > peak_gain_atr:
                peak_gain_atr = bar_peak_atr
                
        print(f"ACTUAL_WINDOW_MAX_PEAK_ATR = {peak_gain_atr}")
    except Exception as e:
        print(e)
else:
    print("Trade not properly matched.")
