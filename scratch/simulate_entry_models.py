import ccxt
import pandas as pd
import numpy as np
import json
from collections import defaultdict
import math

def get_data():
    exchange = ccxt.binanceusdm()
    ohlcv = exchange.fetch_ohlcv('1000LUNC/USDT', '1m', limit=1500)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['tr'] = np.maximum(df['high'] - df['low'], 
                          np.maximum(abs(df['high'] - df['close'].shift()), 
                                     abs(df['low'] - df['close'].shift())))
    df['atr'] = df['tr'].rolling(window=14).mean()
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma15'] = df['close'].rolling(15).mean()
    df['kc_middle'] = df['close'].rolling(20).mean()
    df['kc_upper'] = df['kc_middle'] + df['atr'] * 2.0
    df['kc_lower'] = df['kc_middle'] - df['atr'] * 2.0
    return df.dropna().reset_index(drop=True)

def forward_eval(df, entry_idx, entry_price, side):
    atr = df.loc[entry_idx-1, 'atr']
    if math.isnan(atr) or atr == 0:
        return {}
    
    future = df.iloc[entry_idx:]
    
    mfe = -999.0
    mae = -999.0
    
    reached = {0.5: False, 1.0: False, 1.5: False, 2.0: False}
    hit_minus_1 = False
    
    for idx, row in future.iterrows():
        if side == 'LONG':
            cur_mfe = (row['high'] - entry_price) / atr
            cur_mae = (entry_price - row['low']) / atr
        else:
            cur_mfe = (entry_price - row['low']) / atr
            cur_mae = (row['high'] - entry_price) / atr
            
        mfe = max(mfe, cur_mfe)
        mae = max(mae, cur_mae)
        
        if not hit_minus_1:
            if cur_mae >= 1.0:
                hit_minus_1 = True
            else:
                for level in reached.keys():
                    if cur_mfe >= level:
                        reached[level] = True
                        
    return {
        'mfe': mfe,
        'mae': mae,
        'reached_0.5': reached[0.5],
        'reached_1.0': reached[1.0],
        'reached_1.5': reached[1.5],
        'reached_2.0': reached[2.0]
    }

def run_simulation(df):
    results = defaultdict(list)
    
    # State tracking
    state_b_long = {'state': 'WAIT_CROSS', 'cross_idx': -1, 'pullback_idx': -1}
    state_b_short = {'state': 'WAIT_CROSS', 'cross_idx': -1, 'pullback_idx': -1}
    
    state_c_long = {'state': 'WAIT_PULLBACK', 'pullback_idx': -1}
    state_c_short = {'state': 'WAIT_PULLBACK', 'pullback_idx': -1}
    
    for i in range(20, len(df)-1):
        # We look at i-1 as the most recently closed bar
        prev = df.loc[i-1]
        prev2 = df.loc[i-2]
        prev3 = df.loc[i-3]
        
        # Current open is entry price if signaled
        entry_price = df.loc[i, 'open']
        ts = df.loc[i, 'timestamp']
        
        # --- MODEL A (Simplified Canonical KC) ---
        # LONG: prev2 open inside/above lower, close above lower, close > open, prev close > prev open
        if prev2['kc_middle'] > prev3['kc_middle'] and prev2['close'] > prev2['kc_upper'] and prev2['close'] > prev2['open'] and prev['close'] > prev['open']:
            res = forward_eval(df, i, entry_price, 'LONG')
            if res: results['A'].append({'side': 'LONG', 'ts': ts, **res})
            
        if prev2['kc_middle'] < prev3['kc_middle'] and prev2['close'] < prev2['kc_lower'] and prev2['close'] < prev2['open'] and prev['close'] < prev['open']:
            res = forward_eval(df, i, entry_price, 'SHORT')
            if res: results['A'].append({'side': 'SHORT', 'ts': ts, **res})

        # --- MODEL B (MA Cross Pullback) ---
        # Check MA Cross
        if prev2['ma5'] <= prev2['ma15'] and prev['ma5'] > prev['ma15']:
            state_b_long = {'state': 'WAIT_PULLBACK', 'cross_idx': i-1, 'pullback_idx': -1}
        if prev2['ma5'] >= prev2['ma15'] and prev['ma5'] < prev['ma15']:
            state_b_short = {'state': 'WAIT_PULLBACK', 'cross_idx': i-1, 'pullback_idx': -1}
            
        # Long Logic B1
        if state_b_long['state'] == 'WAIT_PULLBACK':
            if prev['ma5'] > prev['ma15'] and prev['low'] <= prev['ma5']:
                state_b_long['state'] = 'PULLBACK_CONFIRMED'
                state_b_long['pullback_idx'] = i-1
            elif prev['ma5'] <= prev['ma15']:
                state_b_long['state'] = 'WAIT_CROSS'
        elif state_b_long['state'] == 'PULLBACK_CONFIRMED':
            if prev['close'] > prev['open']:
                res = forward_eval(df, i, entry_price, 'LONG')
                if res: results['B1'].append({'side': 'LONG', 'ts': ts, **res})
                state_b_long['state'] = 'WAIT_CROSS'
            elif prev['ma5'] <= prev['ma15']:
                state_b_long['state'] = 'WAIT_CROSS'

        # Short Logic B1
        if state_b_short['state'] == 'WAIT_PULLBACK':
            if prev['ma5'] < prev['ma15'] and prev['high'] >= prev['ma5']:
                state_b_short['state'] = 'PULLBACK_CONFIRMED'
                state_b_short['pullback_idx'] = i-1
            elif prev['ma5'] >= prev['ma15']:
                state_b_short['state'] = 'WAIT_CROSS'
        elif state_b_short['state'] == 'PULLBACK_CONFIRMED':
            if prev['close'] < prev['open']:
                res = forward_eval(df, i, entry_price, 'SHORT')
                if res: results['B1'].append({'side': 'SHORT', 'ts': ts, **res})
                state_b_short['state'] = 'WAIT_CROSS'
            elif prev['ma5'] >= prev['ma15']:
                state_b_short['state'] = 'WAIT_CROSS'
                
        # --- MODEL C1 (Trend Structure Pullback) ---
        if prev['ma5'] > prev['ma15'] and prev['ma5'] > prev2['ma5'] and prev['ma15'] >= prev2['ma15'] and prev['close'] > prev['kc_middle']:
            if prev['low'] <= prev['ma5']:
                state_c_long['state'] = 'PULLBACK_CONFIRMED'
        elif state_c_long['state'] == 'PULLBACK_CONFIRMED':
            if prev['close'] > prev['open']:
                res = forward_eval(df, i, entry_price, 'LONG')
                if res: results['C1'].append({'side': 'LONG', 'ts': ts, **res})
                state_c_long['state'] = 'WAIT_PULLBACK'
            elif prev['ma5'] <= prev['ma15']:
                state_c_long['state'] = 'WAIT_PULLBACK'

        if prev['ma5'] < prev['ma15'] and prev['ma5'] < prev2['ma5'] and prev['ma15'] <= prev2['ma15'] and prev['close'] < prev['kc_middle']:
            if prev['high'] >= prev['ma5']:
                state_c_short['state'] = 'PULLBACK_CONFIRMED'
        elif state_c_short['state'] == 'PULLBACK_CONFIRMED':
            if prev['close'] < prev['open']:
                res = forward_eval(df, i, entry_price, 'SHORT')
                if res: results['C1'].append({'side': 'SHORT', 'ts': ts, **res})
                state_c_short['state'] = 'WAIT_PULLBACK'
            elif prev['ma5'] >= prev['ma15']:
                state_c_short['state'] = 'WAIT_PULLBACK'
                
    return results

def print_summary(results):
    for model, records in results.items():
        if not records:
            continue
        n = len(records)
        long_n = len([r for r in records if r['side'] == 'LONG'])
        short_n = len([r for r in records if r['side'] == 'SHORT'])
        
        med_mfe = np.median([r['mfe'] for r in records])
        med_mae = np.median([r['mae'] for r in records])
        
        p50_mfe = np.percentile([r['mfe'] for r in records], 50)
        p75_mfe = np.percentile([r['mfe'] for r in records], 75)
        
        r05 = sum(1 for r in records if r['reached_0.5']) / n * 100
        r10 = sum(1 for r in records if r['reached_1.0']) / n * 100
        r15 = sum(1 for r in records if r['reached_1.5']) / n * 100
        r20 = sum(1 for r in records if r['reached_2.0']) / n * 100
        
        print(f"[{model}] N: {n} (L: {long_n} S: {short_n})")
        print(f"Med MFE: {med_mfe:.2f} ATR | Med MAE: {med_mae:.2f} ATR")
        print(f"MFE P50: {p50_mfe:.2f} | P75: {p75_mfe:.2f}")
        print(f"Reached +0.5 before -1: {r05:.1f}%")
        print(f"Reached +1.0 before -1: {r10:.1f}%")
        print(f"Reached +1.5 before -1: {r15:.1f}%")
        print(f"Reached +2.0 before -1: {r20:.1f}%")
        print("-" * 40)

if __name__ == '__main__':
    df = get_data()
    res = run_simulation(df)
    print_summary(res)
    
    # Save detailed
    with open('reports/sim_results.json', 'w') as f:
        json.dump(res, f, indent=2)
