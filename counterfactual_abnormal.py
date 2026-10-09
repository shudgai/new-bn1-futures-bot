import json
import requests
import datetime
import time
import pandas as pd
import numpy as np
import math

from core.strategy import SuperTrendKeltnerStrategy
import core.services.exits.peak_trailing_exit as pte
from core.guards.abnormal_guard import channel_adverse_exit_reason

# Original function backup
orig_adverse = channel_adverse_exit_reason

def patched_adverse(*args, **kwargs):
    res = orig_adverse(*args, **kwargs)
    if res == 'EMERGENCY_EXIT_2_CANDLE_ADVERSE':
        return None # DISABLE ABNORMAL_BODY
    return res

pte.channel_adverse_exit_reason = patched_adverse

def get_historical_data(symbol, end_time_ms):
    url = 'https://fapi.binance.com/fapi/v1/klines'
    sym = symbol.replace('/', '')
    if sym == '龙虾USDT': return None # Excluded
    params = {'symbol': sym, 'interval': '1m', 'endTime': end_time_ms + 10*60000, 'limit': 150}
    r = requests.get(url, params=params)
    if r.status_code != 200: return None
    data = []
    for k in r.json():
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
    # ot is OPEN trade, ct is CLOSE trade (abnormal body)
    exit_ms = ct['id']
    # df has bars up to exit_ms + 10m
    
    # State tracking
    position = dict(ot)
    position['side'] = ot['side']
    position['open_timestamp'] = ot['id']
    position['entry_price'] = ot['price']
    position['initial_sl'] = ot.get('sl', ot.get('initial_sl', 0))
    position['sl'] = position['initial_sl']
    
    exit_reason = None
    exit_price = None
    exit_time = None
    
    # Simulation start from the bar containing exit_ms
    start_idx = -1
    for i in range(len(df)):
        if df.iloc[i]['timestamp'] <= exit_ms < df.iloc[i]['timestamp'] + 60000:
            start_idx = i
            break
            
    if start_idx == -1: return None
    
    sign = 1 if position['side'] == 'LONG' else -1
    qty = float(position.get('qty', 1))
    
    for i in range(start_idx, min(start_idx + 11, len(df))):
        if exit_reason: break
        
        live_bar = df.iloc[i]
        bar_ms = live_bar['timestamp']
        
        # We need the snapshot of the PREVIOUS closed bar for indicator values
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
        
        # Simulate ticks using High and Low (conservative evaluation)
        ticks = [live_bar['open'], live_bar['high'], live_bar['low'], live_bar['close']]
        if position['side'] == 'LONG':
            ticks = [live_bar['open'], live_bar['low'], live_bar['high'], live_bar['close']] # Test worst first
        else:
            ticks = [live_bar['open'], live_bar['high'], live_bar['low'], live_bar['close']]
            
        for price in ticks:
            # Check hard stop manually as engine does
            sl = float(position.get('sl', 0))
            if sl > 0:
                if (position['side'] == 'LONG' and price <= sl) or (position['side'] == 'SHORT' and price >= sl):
                    exit_reason = 'EXIT_INITIAL_ATR_HARD_STOP'
                    exit_price = sl
                    exit_time = bar_ms
                    break
                    
            # Call peak trailing exit evaluator
            # Need to provide frame for abnormal_guard inside peak_trailing_exit
            # We must set up df up to i-1 as closed, and i as live
            current_df = df.iloc[:i].copy()
            current_df['is_closed'] = True
            
            res = pte.evaluate_peak_trailing(position, price, snapshot, atr=snapshot['atr'])
            if res:
                action, reason = res
                if action == 'EXIT':
                    exit_reason = reason
                    exit_price = price
                    exit_time = bar_ms
                    break

    # If it survived 10 bars, force exit at 10-bar close
    if not exit_reason:
        last_bar = df.iloc[min(start_idx + 10, len(df)-1)]
        exit_reason = 'SURVIVED_10_BARS'
        exit_price = last_bar['close']
        exit_time = last_bar['timestamp']
        
    # Calculate PnL
    raw_pnl = sign * (exit_price - float(position['entry_price'])) * qty
    fee = (float(position['entry_price']) * qty * 0.0005) + (exit_price * qty * 0.0005)
    slippage = (float(position['entry_price']) * qty * 0.0001) + (exit_price * qty * 0.0001)
    net_pnl = raw_pnl - fee - slippage
    
    return {
        'reason': exit_reason,
        'pnl': net_pnl,
        'exit_time': exit_time
    }

def main():
    try:
        with open('data/paper_account.json', 'r') as f:
            paper_data = json.load(f)
            trades = paper_data.get('trades', [])
    except:
        trades = []

    open_trades = [t for t in trades if t.get('action') in ('OPEN_LONG', 'OPEN_SHORT')]
    close_trades = [t for t in trades if t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT')]

    # Exact one-to-one pairing
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
            
    proven_pairs = [p for p in abnormal_pairs if '龙虾' not in p['close']['symbol'] and '龍蝦' not in p['close']['symbol']]
    
    print("CURRENT PRODUCTION")
    print(f"N: {len(proven_pairs)}")
    orig_pnls = [p['close'].get('pnl', 0) for p in proven_pairs]
    print(f"Total PnL: {sum(orig_pnls):.4f}")
    print(f"Avg PnL: {np.mean(orig_pnls):.4f}")
    print(f"Median PnL: {np.median(orig_pnls):.4f}")
    
    # Calculate orig R
    orig_rs = []
    for p in proven_pairs:
        sl = p['open'].get('sl', p['open'].get('initial_sl', 0))
        entry = p['open']['price']
        if sl > 0 and entry != sl:
            r = p['close'].get('pnl', 0) / abs((entry - sl) * float(p['open'].get('qty', 1)))
            orig_rs.append(r)
    if orig_rs:
        print(f"Avg R: {np.mean(orig_rs):.4f}")
        print(f"Median R: {np.median(orig_rs):.4f}")
    else:
        print("R calculation NA")

    print("\nPRODUCTION MINUS ABNORMAL_BODY (True Replay)")
    replay_results = []
    
    for p in proven_pairs:
        df = get_historical_data(p['open']['symbol'], p['close']['id'])
        if df is None: continue
        res = simulate_trade(p['open'], p['close'], df)
        if res:
            replay_results.append(res)
            
    if replay_results:
        print(f"N: {len(replay_results)}")
        new_pnls = [r['pnl'] for r in replay_results]
        print(f"Total PnL: {sum(new_pnls):.4f}")
        print(f"Avg PnL: {np.mean(new_pnls):.4f}")
        print(f"Median PnL: {np.median(new_pnls):.4f}")
        
        new_rs = []
        for i, r in enumerate(replay_results):
            p = proven_pairs[i]
            sl = p['open'].get('sl', p['open'].get('initial_sl', 0))
            entry = p['open']['price']
            if sl > 0 and entry != sl:
                r_val = r['pnl'] / abs((entry - sl) * float(p['open'].get('qty', 1)))
                new_rs.append(r_val)
        if new_rs:
            print(f"Avg R: {np.mean(new_rs):.4f}")
            print(f"Median R: {np.median(new_rs):.4f}")
            
        print("\nExit reason distribution:")
        reasons = [r['reason'] for r in replay_results]
        for reason in set(reasons):
            print(f"{reason}: {reasons.count(reason)}")
            
        better = sum(1 for i, r in enumerate(replay_results) if r['pnl'] > proven_pairs[i]['close'].get('pnl', 0))
        worse = sum(1 for i, r in enumerate(replay_results) if r['pnl'] < proven_pairs[i]['close'].get('pnl', 0))
        unchanged = sum(1 for i, r in enumerate(replay_results) if r['pnl'] == proven_pairs[i]['close'].get('pnl', 0))
        
        print(f"\nbetter trade count: {better}")
        print(f"worse trade count: {worse}")
        print(f"unchanged trade count: {unchanged}")

if __name__ == '__main__':
    main()
