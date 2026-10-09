import json

with open('data/paper_account.json', 'r') as f:
    state = json.load(f)

trade_id = 1790968085827
t_open = None
for t in state.get('trades', []):
    if t.get('id') == trade_id and t.get('action').startswith('OPEN'):
        t_open = t

cbid = t_open.get('channel_confirmation_bar_id')

closes = []
for t in state.get('trades', []):
    if t.get('action') == 'CLOSE_SHORT' and t.get('symbol') == '龙虾/USDT':
        if t.get('channel_confirmation_bar_id') == cbid:
            closes.append(t)

if not closes:
    print("CLOSE_ID = NOT_FOUND")
else:
    for t_close in closes:
        print(f"CLOSE_ID = {t_close.get('id')}")
        print(f"CLOSE_TIME = {t_close.get('id')} ({t_close.get('time')})")
        print(f"CLOSE_PRICE = {t_close.get('price')}")
        print(f"CLOSE_NET_PNL = {t_close.get('pnl')}")
        print(f"CLOSE_REASON = {t_close.get('reason')}")
