from mass_replay import fetch_klines, get_indicators
from core.services.kc_pending_entry import evaluate_kc_pending_entry
import pandas as pd
from collections import defaultdict

df = fetch_klines("BTCUSDT", 7)
df = df.iloc[:-1].copy()
df_ind = get_indicators(df)

raw_enter = 0
unique_accepted = set()
contract_accepted = 0
contract_rejected = 0
first_entry = None

for i in range(50, len(df_ind)):
    window = df_ind.iloc[i-50:i+1].copy()
    quote = window.iloc[-1]['close']
    res = evaluate_kc_pending_entry(window, quote, symbol="BTCUSDT")
    if res.get('action') == 'ENTER':
        raw_enter += 1
        signal_id = res['pending_signal_id']
        
        if signal_id not in unique_accepted:
            unique_accepted.add(signal_id)
            contract_accepted += 1
            if first_entry is None:
                first_entry = res
                first_entry['bar3'] = window.iloc[-1]
                first_entry['bar1'] = window[window['timestamp'] == res['breakout_bar_id']].iloc[0]
        else:
            contract_rejected += 1

print(f"RAW_EVALUATOR_ENTER = {raw_enter}")
print(f"CONTRACT_ACCEPTED_ENTER = {contract_accepted}")
print(f"CONTRACT_REJECTED_DUPLICATE = {contract_rejected}")
print(f"UNIQUE_ACCEPTED_SIGNAL_ID = {len(unique_accepted)}")
if first_entry:
    atr = first_entry['bar3']['atr']
    sign = 1 if first_entry['side'] == 'LONG' else -1
    price = first_entry['price']
    sl = price - sign * 1.5 * atr
    print(f"FIRST REAL SIGNAL INITIAL_STOP = {sl}")
    print(f"FIRST REAL SIGNAL ENTRY_PRICE = {price}")
    print(f"FIRST REAL SIGNAL ATR = {atr}")

