import json
import pandas as pd
from unittest.mock import patch
import sys
sys.path.append('.')
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
import core.services.exits.peak_trailing_exit as pte

with open('data/paper_account.json', 'r') as f:
    state = json.load(f)

trade = next((t for t in state.get('trades', []) if str(t['id']) == '1790968085827'), None)
entry_price = float(trade['price'])
atr = 0.000664
print(f"Trade Entry: {entry_price} Time: {trade['id']}")

df = pd.read_csv('scratch/lobster_real_1m_history.csv')

df['ma3'] = df['close'].rolling(3).mean()
df['ma5'] = df['close'].rolling(5).mean()
df['ma15'] = df['close'].rolling(15).mean()
df['kc_mid'] = df['close'].rolling(20).mean()
# The true trade df needs to be recalculated over the whole history so we don't have NaNs for the trade window.
df_full = pd.read_csv('scratch/lobster_real_1m_history.csv').sort_values('timestamp').reset_index(drop=True)
df_full['ma3'] = df_full['close'].rolling(3).mean()
df_full['ma5'] = df_full['close'].rolling(5).mean()
df_full['ma15'] = df_full['close'].rolling(15).mean()
df_full['kc_mid'] = df_full['close'].rolling(20).mean()
df = df_full[(df_full['timestamp'] >= trade['id'])].reset_index(drop=True)


pos = {
    'symbol': 'LOBSTER/USDT',
    'side': 'SHORT',
    'entry_price': entry_price,
    'qty': float(trade['qty']),
    'opened_at': trade['id'],
    'state': {}
}

# Real trend hold
from core.services.exits.trend_hold_evaluator import evaluate_trend_hold
real_evaluate_trend_hold = evaluate_trend_hold

found_arm = None
found_trigger = None

cf_exit_time = None
cf_exit_price = None

for i in range(len(df)):
    bar = df.iloc[i]
    ts = bar['timestamp']
    close_price = bar['close']
    low_price = bar['low']
    high_price = bar['high']
    
    # Intrabar sequence
    for p in [open, high_price, low_price, close_price]:
        if p == open: p = bar['open']
        gain = entry_price - p
        gain_atr = gain / atr
        
        if gain_atr >= 3.0 and not found_arm:
            found_arm = ts
            print(f"PARABOLIC_ARM_TIME = {ts}")
            print(f"ARM_PRICE = {p}")
        
        snapshot = {
            'ma3': bar['ma3'], 'ma5': bar['ma5'], 'ma15': bar['ma15'], 'kc_middle': bar['kc_mid'],
            'last_ma5': df.iloc[i-1]['ma5'] if i > 0 else bar['ma5'],
            'last_open': df.iloc[i-1]['open'] if i > 0 else bar['open'],
            'last_high': df.iloc[i-1]['high'] if i > 0 else bar['high'],
            'last_low': df.iloc[i-1]['low'] if i > 0 else bar['low'],
            'last_close': df.iloc[i-1]['close'] if i > 0 else bar['close'],
        }
        
        # Test PRE-veto (mock trend hold to RELEASE)
        pos['state']['temp_pre'] = True
        pre_reason, pre_trigger = None, None
        
        res = evaluate_peak_trailing(pos, p, snapshot, atr=atr)
        if res and res.get('trigger') == 'EXIT_PARABOLIC_PULLBACK_1_ATR':
            pre_reason = res.get('reason')
            pre_trigger = res.get('trigger')
        del pos['state']['temp_pre']
        
        # Test POST-veto (actual)
        res_post = evaluate_peak_trailing(pos, p, snapshot, atr=atr)
        
        if pre_trigger == 'EXIT_PARABOLIC_PULLBACK_1_ATR' and not found_trigger:
            found_trigger = True
            giveback_atr = (p - pos['state']['peak_price']) / atr
            trend_status, _ = real_evaluate_trend_hold(snapshot, p, 'SHORT', 'LOBSTER/USDT', trade['id'], pos['state'])
            print(f"PARABOLIC_TRIGGER_TIME = {ts}")
            print(f"PEAK_GAIN_ATR = {(entry_price - pos['state']['peak_price'])/atr}")
            print(f"GIVEBACK_ATR = {giveback_atr}")
            print(f"TREND_STATUS_AT_TRIGGER = {trend_status}")
            print(f"PRE_VETO_REASON = {pre_reason}")
            print(f"PRE_VETO_TRIGGER = {pre_trigger}")
            post_r = res_post['reason'] if res_post else 'None'
            post_t = res_post['trigger'] if res_post else 'None'
            print(f"POST_VETO_REASON = {post_r}")
            print(f"POST_VETO_TRIGGER = {post_t}")
            
            # Counterfactual
            cf_exit_time = ts
            cf_exit_price = p
            cf_net = (entry_price - p) * float(trade['qty']) - float(trade['fee']) - (p * float(trade['qty']) * 0.0005)
            print(f"COUNTERFACTUAL_EXIT_TIME = {cf_exit_time}")
            print(f"COUNTERFACTUAL_EXIT_PRICE = {cf_exit_price}")
            print(f"COUNTERFACTUAL_NET_PNL = {cf_net}")
            break
            
    if found_trigger: break

print(f"ACTUAL_EXIT_TIME = {next((t['id'] for t in state.get('trades', []) if t.get('pair_id') == trade['id'] and t.get('action') == 'CLOSE_SHORT'), 'NOT_FOUND')}")
print(f"ACTUAL_EXIT_PRICE = {next((t['price'] for t in state.get('trades', []) if t.get('pair_id') == trade['id'] and t.get('action') == 'CLOSE_SHORT'), 'NOT_FOUND')}")
print(f"ACTUAL_NET_PNL = {next((t['pnl'] for t in state.get('trades', []) if t.get('pair_id') == trade['id'] and t.get('action') == 'CLOSE_SHORT'), 'NOT_FOUND')}")
