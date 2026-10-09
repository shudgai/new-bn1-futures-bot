import json
import asyncio
import aiohttp
import time
import numpy as np

with open('data/paper_account.json', 'r') as f:
    data = json.load(f)

trades = data.get('trades', [])

def extract_trades(symbol):
    t_open = []
    t_close = {}
    for t in trades:
        if symbol in t.get('symbol', ''):
            if t.get('action', '').startswith('OPEN'):
                t_open.append(t)
            elif t.get('action', '').startswith('CLOSE'):
                es = t.get('entry_snapshot')
                if es:
                    cbid = es.get('candidate_bar_id')
                    if cbid:
                        t_close[cbid] = t
    return t_open, t_close

pepe_open, pepe_close = extract_trades('1000PEPE')
lob_open, lob_close = extract_trades('龙虾')

async def fetch_klines(session, symbol, start_time, end_time):
    url = "https://fapi.binance.com/fapi/v1/klines"
    s = symbol.replace('/', '')
    params = {
        "symbol": s,
        "interval": "1m",
        "startTime": int(start_time),
        "endTime": int(end_time + 60000),  # Add a minute buffer
        "limit": 1000
    }
    try:
        async with session.get(url, params=params) as resp:
            return await resp.json()
    except Exception as e:
        return []

async def process_all():
    async with aiohttp.ClientSession() as session:
        all_trades = [("PEPE", t, pepe_close) for t in pepe_open] + [("LOBSTER", t, lob_close) for t in lob_open]
        tasks = []
        valid_trades = []
        for name, t, close_dict in all_trades:
            es = t.get('entry_snapshot') or {}
            entry_time = t.get('time_ms') or es.get('finality_server_ms') or t.get('id')
            cbid = es.get('candidate_bar_id')
            ct = close_dict.get(cbid)
            if entry_time and ct:
                close_time = ct.get('time_ms') or ct.get('id')
                tasks.append(fetch_klines(session, t.get('symbol', ''), entry_time, close_time))
                valid_trades.append((name, t, ct, entry_time, close_time))
            else:
                tasks.append(asyncio.sleep(0, result=[]))
                valid_trades.append((name, t, None, 0, 0))
                
        results = await asyncio.gather(*tasks)
        
        metrics = {"PEPE": {"usable": 0, "mfe_atr": [], "mfe_pct": [], "capture_ratios": [], "total": len(pepe_open)},
                   "LOBSTER": {"usable": 0, "mfe_atr": [], "mfe_pct": [], "capture_ratios": [], "total": len(lob_open)}}
        
        for i, (name, t, ct, entry_time, close_time) in enumerate(valid_trades):
            if not ct: continue
            klines = results[i]
            
            entry_p = float(t.get('price', 0))
            entry_atr = float(t.get('entry_atr') or 0)
            side = t.get('side', 'LONG')
            qty = float(t.get('qty', 0))
            
            # Find the strict next full minute boundary
            next_full_minute = entry_time - (entry_time % 60000) + 60000
            
            valid_klines = [k for k in klines if int(k[0]) >= next_full_minute and int(k[0]) <= close_time]
            
            highs = [float(k[2]) for k in valid_klines]
            lows = [float(k[3]) for k in valid_klines]
            
            # If closed in less than a minute, MFE is based on exit price vs entry price (fallback)
            if not highs:
                exit_p = float(ct.get('price', entry_p))
                if side == 'LONG': mfe = max(0, exit_p - entry_p)
                else: mfe = max(0, entry_p - exit_p)
            else:
                if side == 'LONG': mfe = max(0, max(highs) - entry_p)
                else: mfe = max(0, entry_p - min(lows))
                
            metrics[name]["usable"] += 1
            if entry_atr > 0:
                metrics[name]["mfe_atr"].append(mfe / entry_atr)
            
            if entry_p > 0:
                mfe_pct = mfe / entry_p
                metrics[name]["mfe_pct"].append(mfe_pct)
                
                # Realized capture
                pnl = float(ct.get('pnl', 0))
                gross_opportunity = mfe_pct * entry_p * qty
                
                if gross_opportunity > 0:
                    metrics[name]["capture_ratios"].append(pnl / gross_opportunity)
                else:
                    # If MFE was exactly 0, capture ratio is meaningless, skip
                    pass

        def med(lst): return np.median(lst) if lst else 0
        
        for name in ["PEPE", "LOBSTER"]:
            d = metrics[name]
            print(f"{name}_CLOSED_TRADES_EVALUATED = {d['usable']}")
            print(f"{name}_MEDIAN_MFE_DURING_HOLD_ATR = {med(d['mfe_atr']):.4f}")
            print(f"{name}_MEDIAN_MFE_DURING_HOLD_PCT = {med(d['mfe_pct'])*100:.4f}%")
            if d['capture_ratios']:
                print(f"{name}_REALIZED_MFE_CAPTURE = {med(d['capture_ratios'])*100:.2f}% (Count: {len(d['capture_ratios'])})")
            else:
                print(f"{name}_REALIZED_MFE_CAPTURE = N/A")
            print("---")

asyncio.run(process_all())
