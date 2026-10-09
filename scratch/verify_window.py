import json

with open('data/paper_account.json', 'r') as f:
    state = json.load(f)

trade_id = 1790968085827
t_open = None
t_close = None

for t in state.get('trades', []):
    if t.get('id') == trade_id and t.get('action').startswith('OPEN'):
        t_open = t
    if t.get('pair_id') == trade_id and t.get('action').startswith('CLOSE'):
        t_close = t

if not t_open:
    print("OPEN_ID = NOT_FOUND")
else:
    print(f"OPEN_ID = {t_open.get('id')}")
    print(f"OPEN_TIME = {t_open.get('id')} ({t_open.get('time')})")
    print(f"OPEN_PRICE = {t_open.get('price')}")

if not t_close:
    print("CLOSE_ID = NOT_FOUND")
else:
    print(f"CLOSE_ID = {t_close.get('id')}")
    print(f"CLOSE_TIME = {t_close.get('id')} ({t_close.get('time')})")
    print(f"CLOSE_PRICE = {t_close.get('price')}")
    print(f"CLOSE_NET_PNL = {t_close.get('pnl')}")
    
print(f"PAIRING_PROVEN = {'YES' if t_open and t_close else 'NO'}")
