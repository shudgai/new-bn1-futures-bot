import json

def audit():
    try:
        with open('data/paper_account.json', 'r') as f:
            trades = json.load(f).get('trades', [])
    except Exception as e:
        print(f"Error loading trades: {e}")
        return

    trades.sort(key=lambda x: x.get('id', 0))

    active = {}
    pairings = []
    
    for t in trades:
        key = (t.get('symbol'), t.get('side'))
        action = t.get('action')
        
        if action in ('OPEN_LONG', 'OPEN_SHORT'):
            active.setdefault(key, []).append(t)
        elif action in ('CLOSE_LONG', 'CLOSE_SHORT'):
            if key in active and len(active[key]) > 0:
                pairings.append({'open': active[key].pop(0), 'close': t})

    grouped_pairings = {}
    for p in pairings:
        key = (p['open']['symbol'], p['open']['side'])
        grouped_pairings.setdefault(key, []).append(p)

    premature_sequences = []

    for key, pairs in grouped_pairings.items():
        for i in range(len(pairs) - 1):
            p1 = pairs[i]
            p2 = pairs[i+1]
            
            t_close = p1['close']['id']
            t_reopen = p2['open']['id']
            
            if t_reopen >= t_close:
                bars_held = (t_close - p1['open']['id']) / 60000.0
                bars_between = (t_reopen - t_close) / 60000.0
                
                if bars_between <= 60:
                    premature_sequences.append({
                        'p1': p1,
                        'p2': p2,
                        'bars_held': bars_held,
                        'bars_between': bars_between
                    })

    print(f"N = {len(premature_sequences)}")
    
    exit_reasons = {}
    for seq in premature_sequences:
        reason = seq['p1']['close'].get('reason', 'UNKNOWN')
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    print("\nEXIT REASON DISTRIBUTION:")
    for r, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
        print(f"{r}: {count}")

    print("\nLIFECYCLE TABLE:")
    
    potential_premature = 0
    protective = 0
    
    for seq in premature_sequences:
        p1 = seq['p1']
        p2 = seq['p2']
        p1_open = p1['open']
        p1_close = p1['close']
        p2_open = p2['open']
        
        o1_time = p1_open.get('time', '')[:19]
        c1_time = p1_close.get('time', '')[:19]
        o2_time = p2_open.get('time', '')[:19]
        
        reason = p1_close.get('reason', '')
        held = int(seq['bars_held'])
        btwn = int(seq['bars_between'])
        pnl = p1_close.get('pnl', 0)
        
        # Calculate hypothetical metrics at second open
        # We need the price of the asset at t_reopen, which is p2_open['price']
        price_at_reopen = float(p2_open['price'])
        price_at_open1 = float(p1_open['price'])
        qty1 = float(p1_open['qty'])
        side = 1 if p1_open['side'] == 'LONG' else -1
        
        hyp_pnl = side * (price_at_reopen - price_at_open1) * qty1
        sl = float(p1_open.get('sl', p1_open.get('initial_sl', 0)))
        
        hyp_r = 0
        if sl > 0 and price_at_open1 != sl:
            hyp_r = hyp_pnl / abs((price_at_open1 - sl) * qty1)
            
        # Very simple classification for demonstration
        if 'HARD_STOP' in reason or 'MARGIN_LOSS' in reason:
            protective += 1
            cat = "PROTECTIVE_EXIT"
        elif 'ABNORMAL_BODY' in reason or 'DOJI' in reason:
            if hyp_r > -1.0: # Didn't hit hard stop hypothetically at reopen
                potential_premature += 1
                cat = "POTENTIAL_PREMATURE_EXIT"
            else:
                protective += 1
                cat = "PROTECTIVE_EXIT"
        else:
            cat = "OTHER"

        print(f"{p1_open['symbol']:<15} {p1_open['side']:<6} {o1_time} {price_at_open1:.5f} {c1_time} {p1_close['price']:.5f} {reason[:25]:<25} {held:<4} {pnl:>7.3f} {o2_time} {price_at_reopen:.5f} {btwn:<4} HYP_PNL:{hyp_pnl:>7.3f} HYP_R:{hyp_r:>6.2f} [{cat}]")

    print(f"\nPOTENTIAL_PREMATURE_EXIT N = {potential_premature}")
    print(f"PROTECTIVE_EXIT N = {protective}")

if __name__ == '__main__':
    audit()
