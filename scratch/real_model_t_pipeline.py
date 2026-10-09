import json
import requests
import time
import os
import csv
from datetime import datetime

os.makedirs('scratch/cache/klines', exist_ok=True)
os.makedirs('scratch/cache/aggtrades', exist_ok=True)

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
    cache_file = f"scratch/cache/aggtrades/{symbol.replace('/', '')}_{start_time}_{end_time}.json"
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
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
        res = requests.get(url, params=params)
        if res.status_code != 200:
            print(f"Error fetching aggTrades: {res.text}")
            break
        data = res.json()
        if not data:
            current_start += 1000 * 60 * 60
            time.sleep(0.1)
            continue
        
        trades.extend(data)
        current_start = data[-1]['T'] + 1
        time.sleep(0.1)
        
    with open(cache_file, 'w') as f:
        json.dump(trades, f)
    return trades

def run_pipeline():
    # 1. Pairing
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
                if entry['id'] >= tid:
                    continue
                if entry.get('entry_atr') is None:
                    continue
                
                paired.append({
                    "symbol": sym,
                    "side": side,
                    "entry_time": entry['id'],
                    "entry_price": entry['price'],
                    "entry_atr": entry['entry_atr'],
                    "exit_time": tid,
                    "exit_price": t['price']
                })
                
    arms = [0.50, 0.75, 1.00, 1.25, 1.50, 2.00]
    distances = [0.25, 0.35, 0.50, 0.75, 1.00, 1.25]
    
    ambiguous_bars = 0
    aggtrade_requests = 0
    aggtrade_resolved = 0
    aggtrade_failed = 0
    unknown_count = 0
    
    results = []
    
    klines_request_count = 0
    klines_rows = 0
    trade_windows_complete = 0
    trade_windows_incomplete = 0

    klines_map = {}
    for t in paired:
        start_t = t['entry_time'] - (t['entry_time'] % 60000)
        end_t = t['exit_time']
        klines = get_fapi_klines(t['symbol'], start_t, end_t)
        klines_request_count += 1
        klines_rows += len(klines)
        if len(klines) > 0:
            trade_windows_complete += 1
            klines_map[t['entry_time']] = klines
        else:
            trade_windows_incomplete += 1
            klines_map[t['entry_time']] = []

    combinations_evaluated = 0
    long_trade_simulations = 0
    short_trade_simulations = 0
    
    for arm in arms:
        for dist in distances:
            combinations_evaluated += 1
            for side in ['LONG', 'SHORT']:
                side_trades = [t for t in paired if t['side'] == side]
                
                sim_results = {
                    'arm_atr': arm,
                    'trailing_distance_atr': dist,
                    'side': side,
                    'sample_count': len(side_trades),
                    'armed_count': 0,
                    'floor_hit_count': 0,
                    'unarmed_count': 0,
                    'resolved_count': 0,
                    'unknown_count': 0,
                    'mfe_atr_sum': 0.0,
                    'exit_gain_atr_sum': 0.0,
                    'giveback_atr_sum': 0.0,
                    'valid_exits': 0
                }
                
                for t in side_trades:
                    if side == 'LONG':
                        long_trade_simulations += 1
                    else:
                        short_trade_simulations += 1
                        
                    klines = klines_map.get(t['entry_time'], [])
                    entry_price = t['entry_price']
                    entry_atr = t['entry_atr']
                    
                    is_armed = False
                    current_floor = None
                    peak_price = entry_price
                    
                    exit_price = None
                    exit_reason = None
                    mfe = entry_price
                    
                    for k in klines:
                        k_open = float(k[1])
                        k_high = float(k[2])
                        k_low = float(k[3])
                        k_close = float(k[4])
                        k_start = int(k[0])
                        k_end = int(k[6])
                        
                        clipped = False
                        if k_end >= t['exit_time']:
                            clipped = True
                            
                        if side == 'LONG':
                            mfe = max(mfe, k_high)
                        else:
                            mfe = min(mfe, k_low)
                            
                        if side == 'LONG':
                            arm_price = entry_price + arm * entry_atr
                            if not is_armed:
                                if k_high >= arm_price:
                                    is_armed = True
                                    peak_price = max(peak_price, k_high)
                                    current_floor = peak_price - dist * entry_atr
                                    if k_low <= current_floor:
                                        ambiguous_bars += 1
                                        aggtrade_requests += 1
                                        trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                                        if trades:
                                            aggtrade_resolved += 1
                                            for tr in trades:
                                                p = float(tr['p'])
                                                peak_price = max(peak_price, p)
                                                current_floor = peak_price - dist * entry_atr
                                                if p <= current_floor:
                                                    exit_price = current_floor
                                                    exit_reason = 'FLOOR'
                                                    break
                                        else:
                                            aggtrade_failed += 1
                                            unknown_count += 1
                                            
                                        if exit_reason:
                                            break
                            else:
                                peak_price = max(peak_price, k_high)
                                candidate_floor = peak_price - dist * entry_atr
                                current_floor = max(current_floor, candidate_floor)
                                if k_low <= current_floor:
                                    ambiguous_bars += 1
                                    aggtrade_requests += 1
                                    trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                                    if trades:
                                        aggtrade_resolved += 1
                                        for tr in trades:
                                            p = float(tr['p'])
                                            peak_price = max(peak_price, p)
                                            candidate_floor = peak_price - dist * entry_atr
                                            current_floor = max(current_floor, candidate_floor)
                                            if p <= current_floor:
                                                exit_price = current_floor
                                                exit_reason = 'FLOOR'
                                                break
                                    else:
                                        aggtrade_failed += 1
                                        unknown_count += 1
                                        
                                    if exit_reason:
                                        break
                        else:
                            # SHORT
                            arm_price = entry_price - arm * entry_atr
                            if not is_armed:
                                if k_low <= arm_price:
                                    is_armed = True
                                    peak_price = min(peak_price, k_low)
                                    current_floor = peak_price + dist * entry_atr
                                    if k_high >= current_floor:
                                        ambiguous_bars += 1
                                        aggtrade_requests += 1
                                        trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                                        if trades:
                                            aggtrade_resolved += 1
                                            for tr in trades:
                                                p = float(tr['p'])
                                                peak_price = min(peak_price, p)
                                                current_floor = peak_price + dist * entry_atr
                                                if p >= current_floor:
                                                    exit_price = current_floor
                                                    exit_reason = 'FLOOR'
                                                    break
                                        else:
                                            aggtrade_failed += 1
                                            unknown_count += 1
                                            
                                        if exit_reason:
                                            break
                            else:
                                peak_price = min(peak_price, k_low)
                                candidate_floor = peak_price + dist * entry_atr
                                current_floor = min(current_floor, candidate_floor)
                                if k_high >= current_floor:
                                    ambiguous_bars += 1
                                    aggtrade_requests += 1
                                    trades = get_fapi_aggtrades(t['symbol'], k_start, k_end)
                                    if trades:
                                        aggtrade_resolved += 1
                                        for tr in trades:
                                            p = float(tr['p'])
                                            peak_price = min(peak_price, p)
                                            candidate_floor = peak_price + dist * entry_atr
                                            current_floor = min(current_floor, candidate_floor)
                                            if p >= current_floor:
                                                exit_price = current_floor
                                                exit_reason = 'FLOOR'
                                                break
                                    else:
                                        aggtrade_failed += 1
                                        unknown_count += 1
                                        
                                    if exit_reason:
                                        break
                                        
                        if exit_reason:
                            break
                        if clipped:
                            exit_price = t['exit_price']
                            exit_reason = 'HISTORICAL'
                            break
                            
                    if not exit_reason:
                        exit_price = t['exit_price']
                        exit_reason = 'HISTORICAL'

                    if is_armed:
                        sim_results['armed_count'] += 1
                    else:
                        sim_results['unarmed_count'] += 1
                        
                    if exit_reason == 'FLOOR':
                        sim_results['floor_hit_count'] += 1
                        
                    if exit_price is not None:
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
                        
                results.append(sim_results)

    with open('parameter_grid_results.csv', 'w') as f:
        f.write("side,arm_atr,trailing_distance_atr,sample_count,armed_count,floor_hit_count,unarmed_count,resolved_count,unknown_count,mean_exit_gain_atr,mean_mfe_atr,mean_giveback_atr,profit_retained_ratio\n")
        for r in results:
            mean_mfe = r['mfe_atr_sum'] / r['valid_exits'] if r['valid_exits'] > 0 else 0
            mean_exit = r['exit_gain_atr_sum'] / r['valid_exits'] if r['valid_exits'] > 0 else 0
            mean_giveback = r['giveback_atr_sum'] / r['valid_exits'] if r['valid_exits'] > 0 else 0
            ratio = mean_exit / mean_mfe if mean_mfe > 0 else 0
            f.write(f"{r['side']},{r['arm_atr']},{r['trailing_distance_atr']},{r['sample_count']},{r['armed_count']},{r['floor_hit_count']},{r['unarmed_count']},{r['sample_count'] - r['unknown_count']},{r['unknown_count']},{mean_exit:.4f},{mean_mfe:.4f},{mean_giveback:.4f},{ratio:.4f}\n")
            
    with open('profit_lock_research_report.md', 'w') as f:
        f.write("# Model T Research Report (REAL)\n")
        f.write(f"KLINES_REQUEST_COUNT: {klines_request_count}\n")
        f.write(f"KLINES_ROWS: {klines_rows}\n")
        f.write(f"TRADE_WINDOWS_COMPLETE: {trade_windows_complete}\n")
        f.write(f"TRADE_WINDOWS_INCOMPLETE: {trade_windows_incomplete}\n")
        f.write(f"AMBIGUOUS_BARS: {ambiguous_bars}\n")
        f.write(f"AGGTRADE_WINDOWS_REQUESTED: {aggtrade_requests}\n")
        f.write(f"AGGTRADE_WINDOWS_RESOLVED: {aggtrade_resolved}\n")
        f.write(f"AGGTRADE_WINDOWS_FAILED: {aggtrade_failed}\n")
        f.write(f"LONG_TRADE_SIMULATIONS: {long_trade_simulations}\n")
        f.write(f"SHORT_TRADE_SIMULATIONS: {short_trade_simulations}\n")
        f.write(f"PARAMETER_COMBINATIONS_ACTUAL: {combinations_evaluated}\n")

    print(json.dumps({
        "KLINES_REQUEST_COUNT": klines_request_count,
        "KLINES_ROWS": klines_rows,
        "TRADE_WINDOWS_COMPLETE": trade_windows_complete,
        "TRADE_WINDOWS_INCOMPLETE": trade_windows_incomplete,
        "AMBIGUOUS_BARS": ambiguous_bars,
        "AGGTRADE_WINDOWS_REQUESTED": aggtrade_requests,
        "AGGTRADE_WINDOWS_RESOLVED": aggtrade_resolved,
        "AGGTRADE_WINDOWS_FAILED": aggtrade_failed,
        "UNKNOWN_COUNT": unknown_count,
        "PARAMETER_COMBINATIONS_ACTUAL": combinations_evaluated,
        "LONG_TRADE_SIMULATIONS": long_trade_simulations,
        "SHORT_TRADE_SIMULATIONS": short_trade_simulations
    }))

if __name__ == "__main__":
    run_pipeline()
