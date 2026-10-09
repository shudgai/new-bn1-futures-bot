import json
import os
import requests
import time
import pandas as pd
from datetime import datetime, timezone

def fetch_klines(symbol, start_time, end_time, interval="1m"):
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
    return klines

def process_trades():
    with open('data/paper_account.json', 'r') as f:
        data = json.load(f)
    trades = data.get("trades", [])
    valid_trades = [t for t in trades if t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT', 'LIQUIDATE_LONG', 'LIQUIDATE_SHORT', 'OPEN_LONG', 'OPEN_SHORT')]
    
    # Simple extraction
    # Since we need to match entry and exit to find duration
    open_trades = {}
    paired = []
    
    for t in sorted(trades, key=lambda x: x['id']):
        sym = t['symbol']
        side = t['side']
        action = t['action']
        if action in ('OPEN_LONG', 'OPEN_SHORT'):
            if (sym, side) not in open_trades:
                open_trades[(sym, side)] = t
        elif action in ('CLOSE_LONG', 'CLOSE_SHORT', 'LIQUIDATE_LONG', 'LIQUIDATE_SHORT'):
            if (sym, side) in open_trades:
                entry = open_trades.pop((sym, side))
                paired.append({
                    "symbol": sym,
                    "side": side,
                    "entry_time": entry['id'],
                    "entry_price": entry['price'],
                    "entry_atr": entry.get('entry_atr'),
                    "exit_time": t['id'],
                    "exit_price": t['price']
                })
    print(f"Total paired trades: {len(paired)}")
    print(f"Longs: {len([x for x in paired if x['side'] == 'LONG'])}")
    print(f"Shorts: {len([x for x in paired if x['side'] == 'SHORT'])}")

if __name__ == "__main__":
    process_trades()
