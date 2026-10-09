import json
with open('data/paper_account.json', 'r') as f:
    state = json.load(f)
trade_id = 1790968085827
t_open = None
t_close = None
for t in state.get('trades', []):
    if t.get('id') == trade_id and t.get('action').startswith('OPEN'):
        t_open = t
for t in state.get('trades', []):
    if t.get('pair_id') == trade_id and t.get('action').startswith('CLOSE'):
        t_close = t
if t_close:
    print(f"CLOSE_ID={t_close['id']}")
else:
    print("NO CLOSE TRADES WITH PAIR_ID")
