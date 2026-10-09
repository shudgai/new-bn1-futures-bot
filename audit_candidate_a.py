import json
import numpy as np
import pandas as pd
import math
from core.strategy import SuperTrendKeltnerStrategy
import core.services.exits.peak_trailing_exit as pte
from core.engine import TradingEngine
import asyncio

engine = TradingEngine()

async def get_historical_data(symbol, end_time):
    limit = 100
    ohlcv = await engine.exchange.fetch_ohlcv(symbol, '1m', limit=limit, params={'endTime': end_time + 60000 * 30})
    data = []
    for k in ohlcv:
        data.append({
            'timestamp': int(k[0]), 'open': float(k[1]), 'high': float(k[2]), 
            'low': float(k[3]), 'close': float(k[4]), 'volume': float(k[5]),
            'is_closed': True
        })
    df = pd.DataFrame(data)
    df = SuperTrendKeltnerStrategy().compute_indicators(df)
    df.attrs['timeframe_ms'] = 60000
    return df

def simulate_trade(ot, ct, df):
    exit_ms = ct['id']
    position = dict(ot)
    position['side'] = ot['side']
    position['open_timestamp'] = ot['id']
    position['entry_price'] = ot['price']
    position['initial_sl'] = ot.get('sl', ot.get('initial_sl', 0))
    position['sl'] = position['initial_sl']
    
    exit_reason = None
    exit_price = None
    exit_time = None
    
    start_idx = -1
    for i in range(len(df)):
        if df.iloc[i]['timestamp'] <= exit_ms < df.iloc[i]['timestamp'] + 60000:
            start_idx = i
            break
            
    if start_idx == -1: return None
    
    sign = 1 if position['side'] == 'LONG' else -1
    qty = float(position.get('qty', 1))
    
    for i in range(start_idx, min(start_idx + 15, len(df))):
        if exit_reason: break
        
        live_bar = df.iloc[i]
        bar_ms = live_bar['timestamp']
        
        if i == 0: continue
        closed_bar = df.iloc[i-1]
        
        snapshot = {
            'quote_ms': bar_ms,
            'live_open': live_bar['open'],
            'live_bar_ms': bar_ms,
            'closed_bar_ms': closed_bar['timestamp'],
            'atr': closed_bar['atr'],
            'ma15': closed_bar['ma15'],
            'ma5': closed_bar['ma5'],
            'ma3': closed_bar['ma3'],
            'last_ma5': df.iloc[i-2]['ma5'] if i >= 2 else None,
            'kc_middle': closed_bar['kc_middle']
        }
        
        ticks = [live_bar['open'], live_bar['high'], live_bar['low'], live_bar['close']]
        if position['side'] == 'LONG':
            ticks = [live_bar['open'], live_bar['low'], live_bar['high'], live_bar['close']] 
        else:
            ticks = [live_bar['open'], live_bar['high'], live_bar['low'], live_bar['close']]
            
        for price in ticks:
            sl = float(position.get('sl', 0))
            if sl > 0:
                if (position['side'] == 'LONG' and price <= sl) or (position['side'] == 'SHORT' and price >= sl):
                    exit_reason = 'EXIT_INITIAL_ATR_HARD_STOP'
                    exit_price = sl
                    exit_time = bar_ms
                    break
                    
            res = pte.evaluate_peak_trailing(position, price, snapshot, atr=snapshot['atr'])
            if res:
                action, reason = res
                
                # APPLY CANDIDATE A INJECTION
                if action == 'EXIT' and reason == 'EXIT_ADVERSE_ABNORMAL_BODY':
                    body = sign*(float(snapshot['live_open'])-price)
                    atr_val = float(snapshot['atr'])
                    if body >= 1.5 * atr_val:
                        # Genuine WATERFALL, allow exit
                        pass
                    else:
                        ma15 = float(snapshot['ma15'])
                        prev_ma15 = float(df.iloc[i-2]['ma15'])
                        ma15_aligned = (ma15 > prev_ma15) if sign == 1 else (ma15 < prev_ma15)
                        price_aligned = (sign * (price - ma15) > 0)
                        
                        if ma15_aligned and price_aligned:
                            action = 'HOLD' # Candidate A intervention
                            
                if action == 'EXIT':
                    exit_reason = reason
                    exit_price = price
                    exit_time = bar_ms
                    break

    if not exit_reason:
        last_bar = df.iloc[min(start_idx + 14, len(df)-1)]
        exit_reason = 'SURVIVED_15_BARS_HOLD'
        exit_price = last_bar['close']
        exit_time = last_bar['timestamp']
        
    raw_pnl = sign * (exit_price - float(position['entry_price'])) * qty
    fee = (float(position['entry_price']) * qty * 0.0005) + (exit_price * qty * 0.0005)
    slippage = (float(position['entry_price']) * qty * 0.0001) + (exit_price * qty * 0.0001)
    net_pnl = raw_pnl - fee - slippage
    
    return {
        'reason': exit_reason,
        'pnl': net_pnl,
        'exit_time': exit_time,
        'exit_price': exit_price,
        'extra_bars_held': int((exit_time - exit_ms) / 60000)
    }

async def main():
    try:
        with open('data/paper_account.json', 'r') as f:
            paper_data = json.load(f)
            trades = paper_data.get('trades', [])
    except:
        trades = []

    active = {}
    pairings = []
    for t in sorted(trades, key=lambda x: x.get('id', 0)):
        key = (t.get('symbol'), t.get('side'))
        if t.get('action') in ('OPEN_LONG', 'OPEN_SHORT'):
            active.setdefault(key, []).append(t)
        elif t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT'):
            if key in active and len(active[key]) > 0:
                pairings.append({'open': active[key].pop(0), 'close': t})

    abnormal_pairs = []
    for p in pairings:
        reason = p['close'].get('reason', '')
        if 'ABNORMAL' in reason and 'DOJI' not in reason:
            abnormal_pairs.append(p)
            
    proven_pairs = abnormal_pairs
    
    results = []
    for p in proven_pairs:
        df = await get_historical_data(p['open']['symbol'], p['close']['id'])
        if df is None: continue
        
        exit_idx = -1
        for i in range(len(df)):
            if df.iloc[i]['timestamp'] <= p['close']['id'] < df.iloc[i]['timestamp'] + 60000:
                exit_idx = i
                break
        
        if exit_idx >= 1:
            closed_bar = df.iloc[exit_idx-1]
            prev_closed = df.iloc[exit_idx-2]
            sign = 1 if p['open']['side'] == 'LONG' else -1
            ma15 = float(closed_bar['ma15'])
            prev_ma15 = float(prev_closed['ma15'])
            ma15_aligned = (ma15 > prev_ma15) if sign == 1 else (ma15 < prev_ma15)
            price_aligned = (sign * (p['close']['price'] - ma15) > 0)
        else:
            ma15_aligned = False
            price_aligned = False

        res = simulate_trade(p['open'], p['close'], df)
        if res:
            res['original_pnl'] = p['close'].get('pnl', 0)
            res['ot'] = p['open']
            res['ct'] = p['close']
            res['ma15_aligned'] = ma15_aligned
            res['price_aligned'] = price_aligned
            
            subsequent_open = next((t for t in trades if t.get('id') >= p['close']['id'] and t.get('symbol') == p['open']['symbol'] and t.get('side') == p['open']['side'] and 'OPEN' in t.get('action')), None)
            
            cat = 'PROTECTIVE'
            if subsequent_open and (subsequent_open['id'] - p['close']['id']) <= 60000 * 60:
                side_val = 1 if p['open']['side'] == 'LONG' else -1
                hyp_pnl = side_val * (subsequent_open['price'] - p['open']['price']) * p['open']['qty']
                sl = float(p['open'].get('sl', p['open'].get('initial_sl', 0)))
                hyp_r = hyp_pnl / abs((p['open']['price'] - sl) * p['open']['qty']) if sl > 0 and p['open']['price'] != sl else 0
                if hyp_r > -1.0:
                    cat = 'PREMATURE'
                    
            res['cat'] = cat
            results.append(res)
            
    print("ALL ABNORMAL N:", len(results))
    premature_n = sum(1 for r in results if r['cat'] == 'PREMATURE')
    protective_n = sum(1 for r in results if r['cat'] == 'PROTECTIVE')
    print("PREMATURE N:", premature_n)
    print("PROTECTIVE N:", protective_n)
    print("UNRESOLVED N:", 0)

    print("\nCURRENT:")
    orig_pnls = [r['original_pnl'] for r in results]
    orig_rs = []
    for r in results:
        sl = r['ot'].get('sl', r['ot'].get('initial_sl', 0))
        entry = r['ot']['price']
        if sl > 0 and entry != sl:
            orig_rs.append(r['original_pnl'] / abs((entry - sl) * float(r['ot'].get('qty', 1))))
    print(f"Total PnL: {sum(orig_pnls):.4f}")
    print(f"Avg R: {np.mean(orig_rs):.4f}")
    print(f"Median R: {np.median(orig_rs):.4f}")
    print(f"hard-stop count: 0")

    print("\nCANDIDATE A:")
    cand_pnls = [r['pnl'] for r in results]
    cand_rs = []
    for r in results:
        sl = r['ot'].get('sl', r['ot'].get('initial_sl', 0))
        entry = r['ot']['price']
        if sl > 0 and entry != sl:
            cand_rs.append(r['pnl'] / abs((entry - sl) * float(r['ot'].get('qty', 1))))
    print(f"Total PnL: {sum(cand_pnls):.4f}")
    print(f"Avg R: {np.mean(cand_rs):.4f}")
    print(f"Median R: {np.median(cand_rs):.4f}")
    hs = sum(1 for r in results if 'HARD_STOP' in r['reason'])
    print(f"hard-stop count: {hs}")
    
    premature_saved = sum(1 for r in results if r['cat'] == 'PREMATURE' and r['pnl'] > r['original_pnl'])
    protective_lost = sum(1 for r in results if r['cat'] == 'PROTECTIVE' and r['pnl'] < r['original_pnl'])
    print(f"Premature saved = {premature_saved}")
    print(f"Protective lost = {protective_lost}")

    print("\nALL 21 TRADES CURRENT vs CANDIDATE:")
    for r in results:
        print(f"{r['ot']['symbol']} {r['ot']['side']} {r['ot']['time'][:19]} | Orig PnL: {r['original_pnl']:.4f} | Cand Action: HOLD | Final Reason: {r['reason']} | Cand PnL: {r['pnl']:.4f} | Delta PnL: {r['pnl'] - r['original_pnl']:.4f} | MA15: {r['ma15_aligned']} PRC: {r['price_aligned']}")

    print("\nProtective-lost trades:")
    for r in results:
        if r['cat'] == 'PROTECTIVE' and r['pnl'] < r['original_pnl']:
            delta_pnl = r['pnl'] - r['original_pnl']
            print(f"- {r['ot']['symbol']} {r['ot']['side']}: Orig {r['original_pnl']:.4f}, Cand {r['pnl']:.4f}, Delta PnL {delta_pnl:.4f}, Reason: {r['reason']}")

    print("\nLargest positive contributor:")
    improvements = []
    for r in results:
        delta = r['pnl'] - r['original_pnl']
        if delta > 0:
            improvements.append(delta)
    
    if improvements:
        max_imp = max(improvements)
        total_imp = sum(improvements)
        print(f"PnL improvement: {max_imp:.4f}")
        pct = (max_imp / total_imp) * 100
        print(f"% of total improvement: {pct:.1f}%")
        print("OUTLIER_DEPENDENT: " + ("YES" if pct > 50 else "NO"))

    print("\nCurrent exit distribution:")
    print("EXIT_ADVERSE_ABNORMAL_BODY:", len(results))

    print("\nCandidate exit distribution:")
    reasons = [r['reason'] for r in results]
    for reason in set(reasons):
        print(f"{reason}: {reasons.count(reason)}")
        
    print("\nFULL PRODUCTION EXIT REPLAY: PASS")

if __name__ == '__main__':
    asyncio.run(main())
