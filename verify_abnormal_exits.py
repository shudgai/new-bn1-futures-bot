import json
import requests
import datetime
import time

def get_klines(symbol, start_time, limit=30):
    url = 'https://fapi.binance.com/fapi/v1/klines'
    sym = symbol.replace('/', '').replace('龙虾', '1000LUNC')
    if sym == '1000LUNCUSDT': sym = '1000LUNCUSDT'
    params = {
        'symbol': sym,
        'interval': '1m',
        'startTime': start_time,
        'limit': limit
    }
    r = requests.get(url, params=params)
    if r.status_code == 200:
        return r.json()
    return []

def calc_pnl(side, entry_price, exit_price, qty):
    sign = 1 if side == 'LONG' else -1
    raw_pnl = sign * (exit_price - entry_price) * qty
    fee = (entry_price * qty * 0.0005) + (exit_price * qty * 0.0005)
    slippage = (entry_price * qty * 0.0001) + (exit_price * qty * 0.0001)
    return raw_pnl - fee - slippage

def main():
    try:
        with open('data/paper_account.json', 'r') as f:
            paper_data = json.load(f)
            trades = paper_data.get('trades', [])
    except:
        trades = []

    open_trades = [t for t in trades if t.get('action') in ('OPEN_LONG', 'OPEN_SHORT')]
    close_trades = [t for t in trades if t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT')]

    abnormal_records = []
    for ct in close_trades:
        reason = ct.get('reason', '')
        if 'ABNORMAL' in reason and 'DOJI' not in reason:
            ot = None
            for o in open_trades:
                if o.get('symbol') == ct.get('symbol') and o.get('side') == ct.get('side') and o.get('id', 0) < ct.get('id', 0):
                    ot = o
            if ot:
                abnormal_records.append({'open': ot, 'close': ct})
                
    print("symbol | side | entry_time | entry_price | qty | orig_exit_time | orig_exit_price | orig_pnl | pnl_3 | pnl_5 | pnl_10 | MFE | MAE | stop_before_3 | stop_before_5 | stop_before_10")
    print("-" * 150)
    
    total_orig = 0
    total_3 = 0
    total_5 = 0
    total_10 = 0
    
    for r in abnormal_records:
        ot = r['open']
        ct = r['close']
        
        symbol = ct['symbol']
        side = ct['side']
        entry_ms = ot['id']
        exit_ms = ct['id']
        entry_price = float(ot['price'])
        exit_price = float(ct['price'])
        qty = float(ot.get('qty', 1))
        orig_pnl = float(ct.get('pnl', 0))
        
        start_ms = (exit_ms // 60000) * 60000
        klines = get_klines(symbol, start_ms, limit=12)
        time.sleep(0.2)
        
        if not klines:
            continue
            
        initial_sl = float(ot.get('sl', 0))
        if initial_sl == 0 and ot.get('initial_sl'):
            initial_sl = float(ot.get('initial_sl'))
            
        def get_pnl_at(bars):
            if len(klines) > bars:
                return calc_pnl(side, entry_price, float(klines[bars][4]), qty)
            return None
            
        pnl_3 = get_pnl_at(3)
        pnl_5 = get_pnl_at(5)
        pnl_10 = get_pnl_at(10)
        
        mfe = -999999
        mae = 999999
        stop_hit_idx = -1
        
        for i, k in enumerate(klines[1:11]):
            h = float(k[2])
            l = float(k[3])
            
            if side == 'LONG':
                mfe = max(mfe, h)
                mae = min(mae, l)
                if initial_sl > 0 and l <= initial_sl and stop_hit_idx == -1:
                    stop_hit_idx = i + 1
            else:
                mfe = min(mfe, l)
                mae = max(mae, h)
                if initial_sl > 0 and h >= initial_sl and stop_hit_idx == -1:
                    stop_hit_idx = i + 1
                    
        sb3 = stop_hit_idx != -1 and stop_hit_idx <= 3
        sb5 = stop_hit_idx != -1 and stop_hit_idx <= 5
        sb10 = stop_hit_idx != -1 and stop_hit_idx <= 10
        
        # Hard-stop aware PnL
        if sb3: pnl_3 = calc_pnl(side, entry_price, initial_sl, qty)
        if sb5: pnl_5 = calc_pnl(side, entry_price, initial_sl, qty)
        if sb10: pnl_10 = calc_pnl(side, entry_price, initial_sl, qty)
        
        total_orig += orig_pnl
        if pnl_3 is not None: total_3 += pnl_3
        if pnl_5 is not None: total_5 += pnl_5
        if pnl_10 is not None: total_10 += pnl_10
        
        print(f"{symbol} | {side} | {entry_ms} | {entry_price:.6f} | {qty:.2f} | {exit_ms} | {exit_price:.6f} | {orig_pnl:.4f} | {pnl_3:.4f} | {pnl_5:.4f} | {pnl_10:.4f} | {mfe:.6f} | {mae:.6f} | {sb3} | {sb5} | {sb10}")

    print("\nSummary (HARD-STOP AWARE):")
    print(f"ABNORMAL_BODY N: {len(abnormal_records)}")
    print(f"原始總 PnL: {total_orig:.4f}")
    print(f"+3 bars counterfactual: {total_3:.4f}")
    print(f"+5 bars counterfactual: {total_5:.4f}")
    print(f"+10 bars counterfactual: {total_10:.4f}")

if __name__ == '__main__':
    main()
