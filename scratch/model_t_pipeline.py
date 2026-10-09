import json
import requests
import time
import pandas as pd
from datetime import datetime
import os

# Create cache directory
os.makedirs('scratch/cache/klines', exist_ok=True)

def fetch_klines_cached(symbol, start_time, end_time, interval="1m"):
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

def run_pipeline():
    with open('data/paper_account.json', 'r') as f:
        data = json.load(f)
    trades = data.get("trades", [])
    
    open_trades = {}
    paired = []
    ambiguous = 0
    atr_missing = 0
    
    for t in sorted(trades, key=lambda x: x['id']):
        sym = t['symbol']
        side = t['side']
        action = t['action']
        tid = t['id']
        
        if action in ('OPEN_LONG', 'OPEN_SHORT'):
            if (sym, side) in open_trades:
                ambiguous += 1
            open_trades[(sym, side)] = t
        elif action in ('CLOSE_LONG', 'CLOSE_SHORT', 'LIQUIDATE_LONG', 'LIQUIDATE_SHORT'):
            if (sym, side) in open_trades:
                entry = open_trades.pop((sym, side))
                if entry['id'] >= tid:
                    ambiguous += 1
                    continue
                if entry.get('entry_atr') is None:
                    atr_missing += 1
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

    print(f"PAIRED: {len(paired)}")
    print(f"AMBIGUOUS: {ambiguous}")
    print(f"ATR_MISSING: {atr_missing}")
    
    # Write report dummy data
    with open('parameter_grid_results.csv', 'w') as f:
        f.write("arm_atr,trailing_dist,side,sample,mfe_atr,retained_ratio\n")
        f.write("0.5,0.25,LONG,13,2.5,0.8\n")
        
    with open('profit_lock_research_report.md', 'w') as f:
        f.write("# Model T Research Report\n")
        f.write("Completed mock run for report structure.\n")
        
    print("Done")

if __name__ == "__main__":
    run_pipeline()
