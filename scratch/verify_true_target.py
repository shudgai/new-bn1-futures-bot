import json
import pandas as pd

with open('data/paper_account.json', 'r') as f:
    state = json.load(f)

trade_id = 1790986989938
t_open = next((t for t in state.get('trades', []) if t['id'] == trade_id), None)
cbid = t_open.get('channel_confirmation_bar_id')
t_close = next((t for t in state.get('trades', []) if t.get('action') == 'CLOSE_SHORT' and t.get('symbol') == '龙虾/USDT' and t.get('channel_confirmation_bar_id') == cbid and t['id'] > t_open['id']), None)

entry = t_open['price']
atr = t_open['entry_atr']
sign = -1
t1 = t_open['id']
t2 = t_close['id']

print(f"OPEN_TIME = {t1} ({t_open['time']})")
print(f"CLOSE_TIME = {t2} ({t_close['time']})")
print(f"ENTRY_PRICE = {entry}, ENTRY_ATR = {atr}, QTY = {t_open['qty']}")
print(f"CLOSE_PNL = {t_close['pnl']}")
print(f"CLOSE_REASON = {t_close['reason']}")

try:
    df_full = pd.read_csv('scratch/lobster_real_1m_history.csv')
    df_window = df_full[(df_full['timestamp'] >= t1 - 60000) & (df_full['timestamp'] <= t2 + 60000)]
    
    peak_gain_atr = 0.0
    for i, row in df_window.iterrows():
        l = row['low']
        bar_peak_atr = (l - entry) * sign / atr
        if bar_peak_atr > peak_gain_atr:
            peak_gain_atr = bar_peak_atr
            
    print(f"ACTUAL_WINDOW_MAX_PEAK_ATR = {peak_gain_atr}")
    max_net = peak_gain_atr * atr * t_open['qty'] - (entry * t_open['qty'] * 0.0005 * 2)
    print(f"CANDLE_THEORETICAL_MAX_PROFIT = {max_net}")
except Exception as e:
    print(e)
