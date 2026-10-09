import json

try:
    with open('data/paper_account.json', 'r') as f:
        data = json.load(f)
        trades = data.get('trades', [])
        
    long_closes = [t for t in trades if t.get('action') == 'CLOSE_LONG']
    long_opens = [t for t in trades if t.get('action') == 'OPEN_LONG']
    
    print(f"Total LONG closes: {len(long_closes)}")
    for ct in long_closes[-5:]:  # get the last 5
        # Find matching open
        symbol = ct.get('symbol')
        ot = next((o for o in reversed(long_opens) if o.get('symbol') == symbol and o.get('id', 0) < ct.get('id', 0)), None)
        
        print("-" * 40)
        if ot:
            print(f"OPEN: ID={ot.get('id')}, Time={ot.get('timestamp')}, Symbol={symbol}, Price={ot.get('price')}")
        print(f"CLOSE: ID={ct.get('id')}, Time={ct.get('timestamp')}, Symbol={symbol}, Price={ct.get('price')}")
        print(f"REASON: {ct.get('reason')}")
        print(f"PnL: {ct.get('pnl')}")
        print(f"Close Meta: {ct.get('metadata')}")
        
except Exception as e:
    print("Error:", e)
