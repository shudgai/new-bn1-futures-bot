import json
import requests
import datetime
import time

def get_klines(symbol, start_time, limit=20):
    url = 'https://fapi.binance.com/fapi/v1/klines'
    sym = symbol.replace('/', '').replace('龙虾', '1000LUNC') # if 龙虾 is LUNC
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

def main():
    try:
        with open('data/paper_account.json', 'r') as f:
            paper_data = json.load(f)
            trades = paper_data.get('trades', [])
    except:
        trades = []

    open_trades = [t for t in trades if t.get('action') in ('OPEN_LONG', 'OPEN_SHORT')]
    close_trades = [t for t in trades if t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT')]

    # Filter for DOJI and ABNORMAL
    records = []
    for ct in close_trades:
        reason = ct.get('reason', '')
        if 'ABNORMAL' in reason or 'DOJI' in reason:
            ot = None
            for o in open_trades:
                if o.get('symbol') == ct.get('symbol') and o.get('side') == ct.get('side') and o.get('id', 0) < ct.get('id', 0):
                    ot = o
            if ot:
                records.append({
                    'open': ot,
                    'close': ct,
                    'type': 'COMBINED' if ('ABNORMAL' in reason and 'DOJI' in reason) else ('DOJI' if 'DOJI' in reason else 'ABNORMAL_BODY')
                })

    groups = {'ABNORMAL_BODY': [], 'DOJI': [], 'COMBINED': []}
    for r in records:
        groups[r['type']].append(r)

    for g_name, g_records in groups.items():
        print(f"\n{g_name}:")
        print(f"N: {len(g_records)}")
        if not g_records:
            print("原始總 PnL: 0")
            continue
        
        orig_pnl = sum([r['close'].get('pnl', 0) for r in g_records])
        print(f"原始總 PnL: {orig_pnl:.4f}")
        
        pnl_3 = []
        pnl_5 = []
        pnl_10 = []
        protected = 0
        cut_profits = 0
        
        for r in g_records:
            ot = r['open']
            ct = r['close']
            
            symbol = ct['symbol']
            side = ct['side']
            exit_ms = ct['id']
            exit_price = ct['price']
            entry_price = ot['price']
            qty = ot.get('qty', 1)
            sign = 1 if side == 'LONG' else -1
            
            # 1m bar timestamp corresponding to the exit_ms
            start_ms = (exit_ms // 60000) * 60000
            klines = get_klines(symbol, start_ms, limit=12)
            time.sleep(0.2)
            
            if not klines:
                continue
                
            # klines are [open_time, open, high, low, close, ...]
            # index 0 is exit bar, index 1 is +1 bar, etc.
            
            def calc_pnl(price):
                return sign * (price - entry_price) * qty
                
            p3 = calc_pnl(float(klines[3][4])) if len(klines) > 3 else 0
            p5 = calc_pnl(float(klines[5][4])) if len(klines) > 5 else 0
            p10 = calc_pnl(float(klines[10][4])) if len(klines) > 10 else 0
            
            pnl_3.append(p3)
            pnl_5.append(p5)
            pnl_10.append(p10)
            
            orig_r_pnl = ct.get('pnl', 0)
            # Find MAE and MFE over 10 bars
            mfe = -999999
            mae = 999999
            hit_stop = False
            
            sl = ot.get('sl', 0)
            if sl == 0 and ot.get('initial_sl'):
                sl = ot.get('initial_sl')
                
            for i, k in enumerate(klines[1:11]):
                h = float(k[2])
                l = float(k[3])
                if side == 'LONG':
                    mfe = max(mfe, h)
                    mae = min(mae, l)
                    if sl > 0 and l <= sl:
                        hit_stop = True
                        break
                else:
                    mfe = min(mfe, l)
                    mae = max(mae, h)
                    if sl > 0 and h >= sl:
                        hit_stop = True
                        break
                        
            if hit_stop or (side == 'LONG' and mae < exit_price) or (side == 'SHORT' and mae > exit_price):
                # The market went against them, so the early exit protected them from a worse loss
                # or worse drawdown.
                # Actually, protected means the exit PnL > +10 bars PnL
                pass
                
            if p10 < orig_r_pnl:
                protected += 1
            elif p10 > orig_r_pnl:
                cut_profits += 1
                
        print(f"+3 bars counterfactual: {sum(pnl_3):.4f}")
        print(f"+5 bars counterfactual: {sum(pnl_5):.4f}")
        print(f"+10 bars counterfactual: {sum(pnl_10):.4f}")
        print(f"被 early exit 保護的交易數: {protected}")
        print(f"被 early exit 砍掉後續利潤的交易數: {cut_profits}")
            
if __name__ == '__main__':
    main()
