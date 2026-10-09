import json

def do_audit():
    try:
        with open('data/paper_account.json', 'r') as f:
            paper_data = json.load(f)
            trades = paper_data.get('trades', [])
    except:
        trades = []

    active_positions = {}
    pairings = []

    for t in sorted(trades, key=lambda x: x.get('id', 0)):
        action = t.get('action')
        symbol = t.get('symbol')
        side = t.get('side')
        if not symbol or not side:
            continue
            
        key = (symbol, side)
        
        if action in ('OPEN_LONG', 'OPEN_SHORT'):
            if key not in active_positions:
                active_positions[key] = []
            active_positions[key].append(t)
            
        elif action in ('CLOSE_LONG', 'CLOSE_SHORT'):
            if key in active_positions and len(active_positions[key]) > 0:
                open_trade = active_positions[key].pop(0)
                pairings.append({
                    'open': open_trade,
                    'close': t
                })
            else:
                print(f"Warning: CLOSE without OPEN for {key} at {t.get('time')}")

    # Check for reuse
    open_ids = [p['open']['id'] for p in pairings]
    close_ids = [p['close']['id'] for p in pairings]
    
    dup_opens = len(open_ids) - len(set(open_ids))
    dup_closes = len(close_ids) - len(set(close_ids))
    
    print("=== PAIRING AUDIT ===")
    print(f"unique OPEN count: {len(set(open_ids))}")
    print(f"unique CLOSE count: {len(set(close_ids))}")
    print(f"duplicate OPEN reuse count: {dup_opens}")
    print(f"duplicate CLOSE reuse count: {dup_closes}")
    print("=====================\n")

    # Filter abnormal body from proper pairings
    abnormal_pairs = []
    for p in pairings:
        reason = p['close'].get('reason', '')
        if 'ABNORMAL' in reason and 'DOJI' not in reason:
            abnormal_pairs.append(p)
            
    # Filter out unproven mappings
    proven_pairs = []
    for p in abnormal_pairs:
        if '龙虾' in p['close']['symbol'] or '龍蝦' in p['close']['symbol']:
            print(f"Excluded unproven symbol mapping: {p['close']['symbol']}")
            continue
        proven_pairs.append(p)
        
    print(f"\nRemaining valid ABNORMAL_BODY trades after exclusion: {len(proven_pairs)}")
    
    for i, p in enumerate(proven_pairs):
        print(f"\nTrade {i+1}:")
        print(f"symbol: {p['open']['symbol']}")
        print(f"side: {p['open']['side']}")
        print(f"open timestamp: {p['open']['id']}")
        print(f"open price: {p['open']['price']}")
        print(f"qty: {p['open'].get('qty', 'Unknown')}")
        print(f"close timestamp: {p['close']['id']}")
        print(f"close price: {p['close']['price']}")
        print(f"exit reason: {p['close'].get('reason')}")
        
if __name__ == '__main__':
    do_audit()
