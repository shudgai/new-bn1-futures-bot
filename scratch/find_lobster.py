import json

with open('data/paper_account.json', 'r') as f:
    state = json.load(f)

opens = [t for t in state.get('trades', []) if t.get('action') == 'OPEN_SHORT' and t.get('symbol') == '龙虾/USDT']
closes = [t for t in state.get('trades', []) if t.get('action') == 'CLOSE_SHORT' and t.get('symbol') == '龙虾/USDT']

# sort by open id (which is timestamp)
opens.sort(key=lambda x: x['id'])

print(f"Total Lobster Shorts: {len(opens)}")
for i, t in enumerate(opens):
    cbid = t.get('channel_confirmation_bar_id')
    # find matching close
    matching_close = None
    for c in closes:
        if c.get('channel_confirmation_bar_id') == cbid and c['id'] > t['id']:
            matching_close = c
            break
            
    print(f"\n--- SHORT #{i+1} ---")
    print(f"TRADE_ID: {t['id']}")
    print(f"OPEN_TIME: {t['id']} ({t.get('time')})")
    print(f"OPEN_PRICE: {t['price']}")
    print(f"QTY: {t['qty']}")
    print(f"ENTRY_ATR: {t.get('entry_atr')}")
    
    if matching_close:
        print(f"MATCHING_CLOSE_ID: {matching_close['id']}")
        print(f"CLOSE_TIME: {matching_close['id']} ({matching_close.get('time')})")
        print(f"CLOSE_PRICE: {matching_close['price']}")
        print(f"NET_PNL: {matching_close.get('pnl')}")
        print(f"HOLD_DURATION: {(matching_close['id'] - t['id']) / 1000}s")
        print(f"CLOSE_REASON: {matching_close.get('reason')}")
    else:
        print("MATCHING_CLOSE_ID: NOT_FOUND")
