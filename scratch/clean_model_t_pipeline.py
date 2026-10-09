import json
import requests
import time
import os
import csv
import pandas as pd
from datetime import datetime

os.makedirs('scratch/cache/klines', exist_ok=True)
os.makedirs('scratch/cache/aggtrades', exist_ok=True)

cache_reused = 0
cache_downloaded = 0
rate_limit_failures = 0

def get_fapi_klines(symbol, start_time, end_time, interval="1m"):
    cache_file = f"scratch/cache/klines/{symbol.replace('/', '')}_{start_time}_{end_time}.json"
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            return json.load(f)
    
    url = "https://fapi.binance.com/fapi/v1/klines"
    klines = []
    current_start = start_time
    while current_start < end_time:
        params = {
            "symbol": symbol.replace("/", "").replace("_", ""),
            "interval": interval,
            "startTime": current_start,
            "endTime": end_time,
            "limit": 1500
        }
        res = requests.get(url, params=params)
        if res.status_code != 200:
            print(f"Error fetching klines: {res.text}")
            break
        data = res.json()
        if not data:
            break
        klines.extend(data)
        current_start = data[-1][0] + 1
        time.sleep(0.1)
    
    with open(cache_file, 'w') as f:
        json.dump(klines, f)
    return klines

def get_fapi_aggtrades(symbol, start_time, end_time):
    global cache_reused, cache_downloaded, rate_limit_failures
    
    cache_file = f"scratch/cache/aggtrades/{symbol.replace('/', '')}_{start_time}_{end_time}.json"
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            cache_reused += 1
            return json.load(f)
            
    url = "https://fapi.binance.com/fapi/v1/aggTrades"
    trades = []
    current_start = start_time
    while current_start < end_time:
        params = {
            "symbol": symbol.replace("/", "").replace("_", ""),
            "startTime": current_start,
            "endTime": min(current_start + 1000 * 60 * 60, end_time),
            "limit": 1000
        }
        
        success = False
        for retry in range(5):
            res = requests.get(url, params=params)
            if res.status_code == 200:
                success = True
                break
            elif res.status_code == 429 or '-1003' in res.text:
                time.sleep(2 * (retry + 1))
            else:
                break
                
        if not success:
            rate_limit_failures += 1
            return None
            
        data = res.json()
        if not data:
            current_start += 1000 * 60 * 60
            time.sleep(0.05)
            continue
            
        trades.extend(data)
        current_start = data[-1]['T'] + 1
        time.sleep(0.05)
        
    cache_downloaded += 1
    with open(cache_file, 'w') as f:
        json.dump(trades, f)
    return trades

def run_analysis():
    klines_map = {}
    with open('data/paper_account.json', 'r') as f:
        data = json.load(f)
    trades_data = data.get("trades", [])
    open_trades = {}
    paired = []
    for t in sorted(trades_data, key=lambda x: x['id']):
        sym = t['symbol']
        side = t['side']
        action = t['action']
        tid = t['id']
        if action in ('OPEN_LONG', 'OPEN_SHORT'):
            open_trades[(sym, side)] = t
        elif action in ('CLOSE_LONG', 'CLOSE_SHORT', 'LIQUIDATE_LONG', 'LIQUIDATE_SHORT'):
            if (sym, side) in open_trades:
                entry = open_trades.pop((sym, side))
                if entry['id'] < tid and entry.get('entry_atr') is not None:
                    paired.append({
                        "symbol": sym,
                        "side": side,
                        "entry_time": entry['id'],
                        "entry_price": entry['price'],
                        "entry_atr": entry['entry_atr'],
                        "exit_time": tid,
                        "exit_price": t['price']
                    })
                    
    for t in paired:
        start_t = t['entry_time'] - (t['entry_time'] % 60000)
        end_t = t['exit_time']
        klines_map[t['entry_time']] = get_fapi_klines(t['symbol'], start_t, end_t)
            
    arms = [0.50, 0.75, 1.00, 1.25, 1.50, 2.00]
    distances = [0.25, 0.35, 0.50, 0.75, 1.00, 1.25]
    
    results = []
    trade_details = []
    
    entry_time_assertions_failed = 0
    simulated_exit_assertions_failed = 0
    
    # MFE logic closure
    def track_mfe(mfe, p, side):
        if side == 'LONG': return max(mfe, p)
        return min(mfe, p)
        
    for arm in arms:
        for dist in distances:
            for side in ['LONG', 'SHORT']:
                side_trades = [t for t in paired if t['side'] == side]
                sim_results = {
                    'arm_atr': arm, 'trailing_distance_atr': dist, 'side': side,
                    'sample_count': len(side_trades), 'armed_count': 0, 'floor_hit_count': 0,
                    'unarmed_count': 0, 'resolved_count': 0, 'unknown_count': 0,
                    'mfe_atr_sum': 0.0, 'exit_gain_atr_sum': 0.0, 'giveback_atr_sum': 0.0,
                    'valid_exits': 0, 'exits': []
                }
                for t in side_trades:
                    klines = klines_map.get(t['entry_time'], [])
                    entry_price = t['entry_price']
                    entry_atr = t['entry_atr']
                    entry_time = t['entry_time']
                    exit_time_hist = t['exit_time']
                    
                    is_armed = False
                    current_floor = None
                    peak_price = entry_price
                    exit_price = None
                    exit_reason = None
                    exit_time = None
                    mfe = entry_price
                    
                    first_processed_event_timestamp = None
                    
                    for k in klines:
                        k_open, k_high, k_low, k_close = float(k[1]), float(k[2]), float(k[3]), float(k[4])
                        k_start, k_end = int(k[0]), int(k[6])
                        clipped = (k_end >= exit_time_hist)
                        
                        is_first_bar = (k_start <= entry_time <= k_end)
                        
                        if is_first_bar:
                            trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                            if trades is None:
                                exit_reason = 'UNKNOWN'
                                break
                            
                            for tr in trades:
                                if tr['T'] < entry_time:
                                    continue
                                if tr['T'] > exit_time_hist:
                                    break
                                
                                if first_processed_event_timestamp is None:
                                    first_processed_event_timestamp = tr['T']
                                    
                                p = float(tr['p'])
                                mfe = track_mfe(mfe, p, side)
                                
                                if side == 'LONG':
                                    arm_price = entry_price + arm * entry_atr
                                    if not is_armed:
                                        if p >= arm_price:
                                            is_armed = True
                                            peak_price = max(peak_price, p)
                                            current_floor = peak_price - dist * entry_atr
                                            if p <= current_floor:
                                                exit_price = current_floor
                                                exit_reason = 'FLOOR'
                                                exit_time = tr['T']
                                                break
                                    else:
                                        peak_price = max(peak_price, p)
                                        candidate_floor = peak_price - dist * entry_atr
                                        current_floor = max(current_floor, candidate_floor)
                                        if p <= current_floor:
                                            exit_price = current_floor
                                            exit_reason = 'FLOOR'
                                            exit_time = tr['T']
                                            break
                                else:
                                    # SHORT
                                    arm_price = entry_price - arm * entry_atr
                                    if not is_armed:
                                        if p <= arm_price:
                                            is_armed = True
                                            peak_price = min(peak_price, p)
                                            current_floor = peak_price + dist * entry_atr
                                            if p >= current_floor:
                                                exit_price = current_floor
                                                exit_reason = 'FLOOR'
                                                exit_time = tr['T']
                                                break
                                    else:
                                        peak_price = min(peak_price, p)
                                        candidate_floor = peak_price + dist * entry_atr
                                        current_floor = min(current_floor, candidate_floor)
                                        if p >= current_floor:
                                            exit_price = current_floor
                                            exit_reason = 'FLOOR'
                                            exit_time = tr['T']
                                            break
                        else:
                            if first_processed_event_timestamp is None:
                                first_processed_event_timestamp = k_start
                                
                            # Normal full bar OHLC
                            if side == 'LONG':
                                mfe = max(mfe, k_high)
                                arm_price = entry_price + arm * entry_atr
                                if not is_armed:
                                    if k_high >= arm_price:
                                        is_armed = True
                                        peak_price = max(peak_price, k_high)
                                        current_floor = peak_price - dist * entry_atr
                                        if k_low <= current_floor:
                                            trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                                            if trades is None:
                                                exit_reason = 'UNKNOWN'
                                                break
                                            for tr in trades:
                                                if tr['T'] > exit_time_hist: break
                                                p = float(tr['p'])
                                                peak_price = max(peak_price, p)
                                                current_floor = peak_price - dist * entry_atr
                                                if p <= current_floor:
                                                    exit_price = current_floor
                                                    exit_reason = 'FLOOR'
                                                    exit_time = tr['T']
                                                    break
                                            if exit_reason: break
                                else:
                                    peak_price = max(peak_price, k_high)
                                    candidate_floor = peak_price - dist * entry_atr
                                    current_floor = max(current_floor, candidate_floor)
                                    if k_low <= current_floor:
                                        trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                                        if trades is None:
                                            exit_reason = 'UNKNOWN'
                                            break
                                        for tr in trades:
                                            if tr['T'] > exit_time_hist: break
                                            p = float(tr['p'])
                                            peak_price = max(peak_price, p)
                                            candidate_floor = peak_price - dist * entry_atr
                                            current_floor = max(current_floor, candidate_floor)
                                            if p <= current_floor:
                                                exit_price = current_floor
                                                exit_reason = 'FLOOR'
                                                exit_time = tr['T']
                                                break
                                        if exit_reason: break
                            else:
                                mfe = min(mfe, k_low)
                                arm_price = entry_price - arm * entry_atr
                                if not is_armed:
                                    if k_low <= arm_price:
                                        is_armed = True
                                        peak_price = min(peak_price, k_low)
                                        current_floor = peak_price + dist * entry_atr
                                        if k_high >= current_floor:
                                            trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                                            if trades is None:
                                                exit_reason = 'UNKNOWN'
                                                break
                                            for tr in trades:
                                                if tr['T'] > exit_time_hist: break
                                                p = float(tr['p'])
                                                peak_price = min(peak_price, p)
                                                current_floor = peak_price + dist * entry_atr
                                                if p >= current_floor:
                                                    exit_price = current_floor
                                                    exit_reason = 'FLOOR'
                                                    exit_time = tr['T']
                                                    break
                                            if exit_reason: break
                                else:
                                    peak_price = min(peak_price, k_low)
                                    candidate_floor = peak_price + dist * entry_atr
                                    current_floor = min(current_floor, candidate_floor)
                                    if k_high >= current_floor:
                                        trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                                        if trades is None:
                                            exit_reason = 'UNKNOWN'
                                            break
                                        for tr in trades:
                                            if tr['T'] > exit_time_hist: break
                                            p = float(tr['p'])
                                            peak_price = min(peak_price, p)
                                            candidate_floor = peak_price + dist * entry_atr
                                            current_floor = min(current_floor, candidate_floor)
                                            if p >= current_floor:
                                                exit_price = current_floor
                                                exit_reason = 'FLOOR'
                                                exit_time = tr['T']
                                                break
                                        if exit_reason: break
                        if exit_reason:
                            break
                        if clipped:
                            exit_price = t['exit_price']
                            exit_reason = 'HISTORICAL'
                            exit_time = t['exit_time']
                            break
                            
                    if not exit_reason:
                        exit_price = t['exit_price']
                        exit_reason = 'HISTORICAL'
                        exit_time = t['exit_time']
                        
                    if first_processed_event_timestamp is not None and first_processed_event_timestamp < entry_time:
                        entry_time_assertions_failed += 1
                    
                    if exit_reason != 'UNKNOWN':
                        if exit_time < entry_time or exit_time > exit_time_hist:
                            simulated_exit_assertions_failed += 1

                    if exit_reason == 'UNKNOWN':
                        sim_results['unknown_count'] += 1
                        continue

                    sim_results['resolved_count'] += 1
                    if is_armed: sim_results['armed_count'] += 1
                    else: sim_results['unarmed_count'] += 1
                    if exit_reason == 'FLOOR': sim_results['floor_hit_count'] += 1
                        
                    sim_results['valid_exits'] += 1
                    if side == 'LONG':
                        mfe_atr = (mfe - entry_price) / entry_atr
                        exit_atr = (exit_price - entry_price) / entry_atr
                    else:
                        mfe_atr = (entry_price - mfe) / entry_atr
                        exit_atr = (entry_price - exit_price) / entry_atr
                        
                    giveback_atr = mfe_atr - exit_atr
                    
                    sim_results['mfe_atr_sum'] += mfe_atr
                    sim_results['exit_gain_atr_sum'] += exit_atr
                    sim_results['giveback_atr_sum'] += max(0, giveback_atr)
                    sim_results['exits'].append(exit_atr)
                    
                    trade_details.append({
                        'trade_id': t['entry_time'], 'symbol': t['symbol'], 'side': side,
                        'arm_atr': arm, 'dist_atr': dist, 'exit_reason': exit_reason,
                        'exit_time': exit_time, 'exit_price': exit_price, 'mfe': mfe,
                        'peak': peak_price, 'floor': current_floor, 'exit_gain_atr': exit_atr
                    })
                results.append(sim_results)

    # Clean Baseline CURRENT PULLBACK
    baseline_results = []
    baseline_details = []
    
    for side in ['LONG', 'SHORT']:
        side_trades = [t for t in paired if t['side'] == side]
        sim_results = {
            'side': side, 'sample_count': len(side_trades), 'exit_count': 0, 'unknown_count': 0,
            'mfe_atr_sum': 0.0, 'exit_gain_atr_sum': 0.0, 'giveback_atr_sum': 0.0, 'valid_exits': 0, 'exits': []
        }
        for t in side_trades:
            klines = klines_map.get(t['entry_time'], [])
            entry_price = t['entry_price']
            entry_atr = t['entry_atr']
            entry_time = t['entry_time']
            exit_time_hist = t['exit_time']
            
            peak_price = entry_price
            exit_price = None
            exit_reason = None
            exit_time = None
            mfe = entry_price
            
            first_processed_event_timestamp = None
            
            for k in klines:
                k_open, k_high, k_low, k_close = float(k[1]), float(k[2]), float(k[3]), float(k[4])
                k_start, k_end = int(k[0]), int(k[6])
                clipped = (k_end >= exit_time_hist)
                
                is_first_bar = (k_start <= entry_time <= k_end)
                
                if is_first_bar:
                    trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                    if trades is None:
                        exit_reason = 'UNKNOWN'
                        break
                    
                    for tr in trades:
                        if tr['T'] < entry_time: continue
                        if tr['T'] > exit_time_hist: break
                        if first_processed_event_timestamp is None: first_processed_event_timestamp = tr['T']
                        
                        p = float(tr['p'])
                        if side == 'LONG':
                            mfe = max(mfe, p)
                            peak_price = max(peak_price, p)
                            peak_atr = (peak_price - entry_price) / entry_atr
                            if peak_atr >= 0.5:
                                dist = 0.60 if peak_atr < 1.0 else 0.50 if peak_atr < 2.0 else 0.40 if peak_atr < 3.0 else 0.35
                                floor = peak_price - dist * entry_atr
                                if p <= floor:
                                    exit_price = floor
                                    exit_reason = 'BASELINE'
                                    exit_time = tr['T']
                                    break
                        else:
                            mfe = min(mfe, p)
                            peak_price = min(peak_price, p)
                            peak_atr = (entry_price - peak_price) / entry_atr
                            if peak_atr >= 0.5:
                                dist = 0.60 if peak_atr < 1.0 else 0.50 if peak_atr < 2.0 else 0.40 if peak_atr < 3.0 else 0.35
                                floor = peak_price + dist * entry_atr
                                if p >= floor:
                                    exit_price = floor
                                    exit_reason = 'BASELINE'
                                    exit_time = tr['T']
                                    break
                    if exit_reason: break
                else:
                    if first_processed_event_timestamp is None: first_processed_event_timestamp = k_start
                    
                    if side == 'LONG':
                        mfe = max(mfe, k_high)
                        peak_price = max(peak_price, k_high)
                        peak_atr = (peak_price - entry_price) / entry_atr
                        if peak_atr >= 0.5:
                            dist = 0.60 if peak_atr < 1.0 else 0.50 if peak_atr < 2.0 else 0.40 if peak_atr < 3.0 else 0.35
                            floor = peak_price - dist * entry_atr
                            if k_low <= floor:
                                trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                                if trades is None:
                                    exit_reason = 'UNKNOWN'
                                    break
                                for tr in trades:
                                    if tr['T'] > exit_time_hist: break
                                    p = float(tr['p'])
                                    peak_price = max(peak_price, p)
                                    peak_atr = (peak_price - entry_price) / entry_atr
                                    dist = 0.60 if peak_atr < 1.0 else 0.50 if peak_atr < 2.0 else 0.40 if peak_atr < 3.0 else 0.35
                                    floor = peak_price - dist * entry_atr
                                    if p <= floor:
                                        exit_price = floor
                                        exit_reason = 'BASELINE'
                                        exit_time = tr['T']
                                        break
                                if exit_reason: break
                    else:
                        mfe = min(mfe, k_low)
                        peak_price = min(peak_price, k_low)
                        peak_atr = (entry_price - peak_price) / entry_atr
                        if peak_atr >= 0.5:
                            dist = 0.60 if peak_atr < 1.0 else 0.50 if peak_atr < 2.0 else 0.40 if peak_atr < 3.0 else 0.35
                            floor = peak_price + dist * entry_atr
                            if k_high >= floor:
                                trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                                if trades is None:
                                    exit_reason = 'UNKNOWN'
                                    break
                                for tr in trades:
                                    if tr['T'] > exit_time_hist: break
                                    p = float(tr['p'])
                                    peak_price = min(peak_price, p)
                                    peak_atr = (entry_price - peak_price) / entry_atr
                                    dist = 0.60 if peak_atr < 1.0 else 0.50 if peak_atr < 2.0 else 0.40 if peak_atr < 3.0 else 0.35
                                    floor = peak_price + dist * entry_atr
                                    if p >= floor:
                                        exit_price = floor
                                        exit_reason = 'BASELINE'
                                        exit_time = tr['T']
                                        break
                                if exit_reason: break
                if exit_reason: break
                if clipped:
                    exit_price = t['exit_price']
                    exit_reason = 'HISTORICAL'
                    exit_time = t['exit_time']
                    break
                    
            if not exit_reason:
                exit_price = t['exit_price']
                exit_reason = 'HISTORICAL'
                exit_time = t['exit_time']
                
            if first_processed_event_timestamp is not None and first_processed_event_timestamp < entry_time:
                entry_time_assertions_failed += 1
            if exit_reason != 'UNKNOWN' and (exit_time < entry_time or exit_time > exit_time_hist):
                simulated_exit_assertions_failed += 1
                
            if exit_reason == 'UNKNOWN':
                sim_results['unknown_count'] += 1
                continue
                
            sim_results['exit_count'] += 1
            sim_results['valid_exits'] += 1
            if side == 'LONG':
                mfe_atr = (mfe - entry_price) / entry_atr
                exit_atr = (exit_price - entry_price) / entry_atr
            else:
                mfe_atr = (entry_price - mfe) / entry_atr
                exit_atr = (entry_price - exit_price) / entry_atr
                
            giveback_atr = mfe_atr - exit_atr
            
            sim_results['mfe_atr_sum'] += mfe_atr
            sim_results['exit_gain_atr_sum'] += exit_atr
            sim_results['giveback_atr_sum'] += max(0, giveback_atr)
            sim_results['exits'].append(exit_atr)
            
            baseline_details.append({
                'trade_id': t['entry_time'], 'symbol': t['symbol'], 'side': side, 'exit_reason': exit_reason,
                'exit_time': exit_time, 'exit_price': exit_price, 'mfe': mfe, 'peak': peak_price, 'exit_gain_atr': exit_atr
            })
        baseline_results.append(sim_results)

    with open('scratch/analysis_dump_clean.json', 'w') as f:
        json.dump({
            'model_t_summary': results, 'model_t_details': trade_details,
            'baseline_summary': baseline_results, 'baseline_details': baseline_details,
            'metadata': {
                'entry_time_assertions_failed': entry_time_assertions_failed,
                'simulated_exit_assertions_failed': simulated_exit_assertions_failed,
                'cache_reused': cache_reused,
                'cache_downloaded': cache_downloaded,
                'rate_limit_failures': rate_limit_failures
            }
        }, f)

if __name__ == '__main__':
    run_analysis()
