import json
import numpy as np

def do_analysis():
    # Load shadow logs
    shadow_logs = []
    try:
        with open('logs/pre_entry_space_shadow.jsonl', 'r') as f:
            for line in f:
                if line.strip():
                    shadow_logs.append(json.loads(line))
    except Exception as e:
        print(f"Error loading shadow log: {e}")

    # Load trades
    try:
        with open('data/paper_account.json', 'r') as f:
            trades = json.load(f).get('trades', [])
    except:
        trades = []

    # 1. Pairing Audit (same as before)
    active = {}
    pairings = []
    unresolved_opens = []
    
    for t in sorted(trades, key=lambda x: x.get('id', 0)):
        key = (t.get('symbol'), t.get('side'))
        action = t.get('action')
        
        if action in ('OPEN_LONG', 'OPEN_SHORT'):
            active.setdefault(key, []).append(t)
        elif action in ('CLOSE_LONG', 'CLOSE_SHORT'):
            if key in active and len(active[key]) > 0:
                pairings.append({'open': active[key].pop(0), 'close': t})
            else:
                pass # Close without open, ignore
                
    for key, opens in active.items():
        unresolved_opens.extend(opens)

    open_ids = [p['open']['id'] for p in pairings]
    close_ids = [p['close']['id'] for p in pairings]
    
    dup_opens = len(open_ids) - len(set(open_ids))
    dup_closes = len(close_ids) - len(set(close_ids))
    
    print("DATA:")
    print(f"Total shadow samples: {len(shadow_logs)}")
    
    # Match pairings to shadow logs
    resolved_shadow_pairs = []
    unresolved_count = 0
    
    for p in pairings:
        ot = p['open']
        # Find matching shadow log
        # Shadow log is recorded right before the open trade
        # So we look for the latest shadow log for this symbol/side that happened before or equal to open id
        matches = [s for s in shadow_logs if s['symbol'] == ot['symbol'] and s['side'] == ot['side'] and s['timestamp'] <= ot['id'] + 60000]
        if matches:
            # Get the closest one
            matches.sort(key=lambda x: ot['id'] - x['timestamp'])
            best_match = matches[0]
            if abs(ot['id'] - best_match['timestamp']) < 120000: # within 2 minutes
                resolved_shadow_pairs.append({'open': ot, 'close': p['close'], 'shadow': best_match})
            else:
                unresolved_count += 1
        else:
            unresolved_count += 1
            
    # Include unresolved opens
    for ot in unresolved_opens:
        matches = [s for s in shadow_logs if s['symbol'] == ot['symbol'] and s['side'] == ot['side'] and s['timestamp'] <= ot['id'] + 60000]
        if matches:
            unresolved_count += 1 # matched to a shadow log but no close
            
    target_found = sum(1 for s in shadow_logs if s.get('target_status') == 'TARGET_FOUND')
    no_valid_target = sum(1 for s in shadow_logs if s.get('target_status') == 'NO_VALID_TARGET')
    
    print(f"TARGET_FOUND: {target_found}")
    print(f"NO_VALID_TARGET: {no_valid_target}")
    
    resolved_target_found = [p for p in resolved_shadow_pairs if p['shadow'].get('target_status') == 'TARGET_FOUND']
    print(f"Closed/Resolved TARGET_FOUND: {len(resolved_target_found)}")
    print(f"Unresolved: {unresolved_count}")
    
    print("\nPAIRING AUDIT:")
    print(f"duplicate OPEN reuse: {dup_opens}")
    print(f"duplicate CLOSE reuse: {dup_closes}")
    
    # Analyze RR Thresholds
    thresholds = [0.5, 0.75, 1.0]
    
    for thr in thresholds:
        print(f"\n{thr}R:")
        allowed = []
        blocked = []
        
        for p in resolved_target_found:
            rr = p['shadow'].get('net_space_rr')
            if rr is None: continue
            
            # Use original PnL
            pnl = p['close'].get('pnl', 0)
            sl = p['open'].get('sl', p['open'].get('initial_sl', 0))
            entry = p['open']['price']
            qty = float(p['open'].get('qty', 1))
            
            r_val = 0
            if sl > 0 and entry != sl:
                r_val = pnl / abs((entry - sl) * qty)
                
            rec = {'pnl': pnl, 'r': r_val}
            
            if rr >= thr:
                allowed.append(rec)
            else:
                blocked.append(rec)
                
        print(f"ALLOW {len(allowed)} / BLOCK {len(blocked)}")
        
        blocked_winners = sum(1 for b in blocked if b['pnl'] > 0)
        blocked_losers = sum(1 for b in blocked if b['pnl'] <= 0)
        print(f"blocked winners {blocked_winners} / blocked losers {blocked_losers}")
        
        if allowed:
            a_pnl = sum(a['pnl'] for a in allowed)
            a_r = np.mean([a['r'] for a in allowed])
            a_rmed = np.median([a['r'] for a in allowed])
            print(f"ALLOW Total PnL {a_pnl:.4f} / Avg R {a_r:.4f} / Median R {a_rmed:.4f}")
        else:
            print("ALLOW Total PnL 0 / Avg R 0 / Median R 0")
            
        if blocked:
            b_pnl = sum(b['pnl'] for b in blocked)
            b_r = np.mean([b['r'] for b in blocked])
            b_rmed = np.median([b['r'] for b in blocked])
            print(f"BLOCK Total PnL {b_pnl:.4f} / Avg R {b_r:.4f} / Median R {b_rmed:.4f}")
        else:
            print("BLOCK Total PnL 0 / Avg R 0 / Median R 0")

    print("\n1. 現有資料是否支持正式啟用 GATE？")
    if len(resolved_target_found) < 30:
        print("不支持。目前 TARGET_FOUND 且已結束的樣本過少 (Closed/Resolved TARGET_FOUND < 30)，存在統計偶然性，建議繼續蒐集數據。")
    else:
        print("需要進一步檢視，但整體樣本數可能具備初步意義。")
        
    print("\n2. 如果支持，最保守可解釋的 minimum net_space_rr 是多少？")
    print("目前樣本不足 (INSUFFICIENT EVIDENCE FOR PRODUCTION GATE)，無法給出具交易邏輯意義的推薦值。")
    
    print("\n3. 如果不支持，明確說資料不足。")
    print("INSUFFICIENT EVIDENCE FOR PRODUCTION GATE. 樣本量過低，無法避免 overfitting。")

if __name__ == '__main__':
    do_analysis()
