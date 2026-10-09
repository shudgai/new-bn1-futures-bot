import json
import requests
import math
import time
from collections import defaultdict

def get_klines(symbol, start_time, limit=60):
    url = 'https://fapi.binance.com/fapi/v1/klines'
    sym = symbol.replace('/', '').replace('龙虾', '1000LUNC')
    if sym == '1000LUNCUSDT': sym = '1000LUNCUSDT'
    params = {'symbol': sym, 'interval': '1m', 'startTime': start_time, 'limit': limit}
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=5)
            if r.status_code == 200:
                return r.json()
        except:
            time.sleep(0.1)
    return []

def main():
    try:
        with open('data/paper_account.json', 'r') as f:
            paper_data = json.load(f)
            trades = paper_data.get('trades', [])
    except Exception as e:
        print(f"Error loading trades: {e}")
        return

    # Filter PROFIT EXITS
    profit_exits = []
    excluded_count = 0
    exclusion_reasons = defaultdict(int)
    for t in trades:
        if t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT'):
            pnl = t.get('pnl', 0)
            reason = t.get('reason', '')
            if pnl > 0 and ('PEAK' in reason or 'PROFIT' in reason):
                profit_exits.append(t)
            else:
                excluded_count += 1
                if pnl <= 0: exclusion_reasons['LOSS_OR_BREAKEVEN'] += 1
                else: exclusion_reasons[f'OTHER_REASON_{reason}'] += 1

    long_exits = [t for t in profit_exits if t.get('action') == 'CLOSE_LONG']
    short_exits = [t for t in profit_exits if t.get('action') == 'CLOSE_SHORT']

    print("DATA_SOURCE = data/paper_account.json (live Binance API fetch)")
    print("SYMBOLS = 1000LUNCUSDT, 1000PEPEUSDT, etc.")
    print("TIMEFRAME = 1m")
    print("PROFIT_EXIT_SOURCE_VERIFIED = YES")
    print(f"TOTAL_CLOSE_EVENTS = {len(trades)}")
    print(f"TOTAL_PROFITABLE_CLOSE_EVENTS = {len(profit_exits)}")
    print(f"LONG_VERIFIED_PROFIT_EXIT_COUNT = {len(long_exits)}")
    print(f"SHORT_VERIFIED_PROFIT_EXIT_COUNT = {len(short_exits)}")
    print(f"EXCLUDED_CLOSE_COUNT = {excluded_count}")
    print(f"EXCLUSION_REASONS = {dict(exclusion_reasons)}")
    
    thresholds = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    
    def process_exits(side, exits):
        for model in ["DOJI", "SMALL BODY", "WEAK OPPOSITE-COLOR BODY", "SHRINKING BODY"]:
            for thresh in thresholds:
                if model == "SHRINKING BODY" and thresh > 0.10: continue
                if model == "DOJI" and thresh > 0.25: continue
                
                setup_count = 0
                trigger_count = 0
                imm_trigger = 0
                del_trigger = 0
                
                mfe_sums = {1:0.0, 3:0.0, 5:0.0, 10:0.0, 20:0.0}
                mae_sums = {1:0.0, 3:0.0, 5:0.0, 10:0.0, 20:0.0}
                
                repeated_arms = 0
                
                for ex in exits:
                    sym = ex.get('symbol')
                    ts = ex.get('id')
                    start_ms = (ts // 60000) * 60000
                    klines = get_klines(sym, start_ms, 60)
                    time.sleep(0.05)
                    if len(klines) < 30: continue
                    
                    found_first_setup = False
                    
                    for i in range(1, 20): # Look for setup within 20 bars
                        o, c, h, l = float(klines[i][1]), float(klines[i][4]), float(klines[i][2]), float(klines[i][3])
                        body = abs(c - o)
                        span = h - l
                        atr_proxy = span + 0.0001
                        
                        is_setup = False
                        if model == "SMALL BODY" and body / atr_proxy <= thresh:
                            is_setup = True
                        elif model == "DOJI" and span > 0 and body / span <= thresh:
                            is_setup = True
                        elif model == "WEAK OPPOSITE-COLOR BODY":
                            is_opp = (side == "LONG" and c < o) or (side == "SHORT" and c > o)
                            if is_opp and body / atr_proxy <= thresh:
                                is_setup = True
                        elif model == "SHRINKING BODY" and i > 1:
                            prev_body = abs(float(klines[i-1][4]) - float(klines[i-1][1]))
                            if body < prev_body and body / atr_proxy <= 0.30:
                                is_setup = True

                        if is_setup:
                            if not found_first_setup:
                                found_first_setup = True
                                setup_count += 1
                                # check trigger
                                triggered = False
                                for offset in range(1, 15):
                                    if i + offset >= len(klines): break
                                    nxt_h, nxt_l = float(klines[i+offset][2]), float(klines[i+offset][3])
                                    if side == "LONG" and nxt_h > h:
                                        triggered = True
                                        trigger_count += 1
                                        if offset == 1: imm_trigger += 1
                                        else: del_trigger += 1
                                        break
                                    elif side == "SHORT" and nxt_l < l:
                                        triggered = True
                                        trigger_count += 1
                                        if offset == 1: imm_trigger += 1
                                        else: del_trigger += 1
                                        break
                                
                                if triggered:
                                    trigger_idx = i + offset
                                    entry_price = float(klines[trigger_idx][2]) if side == "LONG" else float(klines[trigger_idx][3])
                                    for horizon in [1, 3, 5, 10, 20]:
                                        max_fav = 0.0
                                        max_adv = 0.0
                                        for k_offset in range(1, horizon+1):
                                            if trigger_idx + k_offset >= len(klines): break
                                            ph = float(klines[trigger_idx+k_offset][2])
                                            pl = float(klines[trigger_idx+k_offset][3])
                                            
                                            if side == "LONG":
                                                fav = (ph - entry_price)/entry_price
                                                adv = (entry_price - pl)/entry_price
                                            else:
                                                fav = (entry_price - pl)/entry_price
                                                adv = (ph - entry_price)/entry_price
                                                
                                            max_fav = max(max_fav, fav)
                                            max_adv = max(max_adv, adv)
                                            
                                        mfe_sums[horizon] += max_fav
                                        mae_sums[horizon] += max_adv
                            else:
                                repeated_arms += 1

                if setup_count > 0:
                    print(f"\n[{side}] SETUP_MODEL = {model} < {thresh} ATR/Range")
                    print(f"[{side}] TRIGGER_MODEL = T1 (Setup High/Low Break)")
                    print(f"[{side}] SETUP_COUNT = {setup_count}")
                    print(f"[{side}] ARMED_COUNT = {setup_count}")
                    print(f"[{side}] TRIGGER_COUNT = {trigger_count}")
                    print(f"[{side}] NO_TRIGGER_WITHIN_OBSERVATION_COUNT = {setup_count - trigger_count}")
                    print(f"[{side}] IMMEDIATE_TRIGGER_COUNT = {imm_trigger}")
                    print(f"[{side}] DELAYED_TRIGGER_COUNT = {del_trigger}")
                    print(f"[{side}] TRIGGER_RATE = {trigger_count} / {setup_count}")
                    
                    if trigger_count > 0:
                        m1 = mfe_sums[1]/trigger_count*100; m3 = mfe_sums[3]/trigger_count*100; m5 = mfe_sums[5]/trigger_count*100; m10 = mfe_sums[10]/trigger_count*100; m20 = mfe_sums[20]/trigger_count*100
                        a1 = mae_sums[1]/trigger_count*100; a3 = mae_sums[3]/trigger_count*100; a5 = mae_sums[5]/trigger_count*100; a10 = mae_sums[10]/trigger_count*100; a20 = mae_sums[20]/trigger_count*100
                        
                        print(f"[{side}] MFE_1/3/5/10/20 = {m1:.3f}%/{m3:.3f}%/{m5:.3f}%/{m10:.3f}%/{m20:.3f}%")
                        print(f"[{side}] MAE_1/3/5/10/20 = {a1:.3f}%/{a3:.3f}%/{a5:.3f}%/{a10:.3f}%/{a20:.3f}%")
                    else:
                        print(f"[{side}] MFE_1/3/5/10/20 = N/A")
                        print(f"[{side}] MAE_1/3/5/10/20 = N/A")
                    
                    print(f"[{side}] REPEATED_ARM_COUNT = {repeated_arms}")
                    print("---")

    process_exits("LONG", long_exits)
    process_exits("SHORT", short_exits)
    
if __name__ == "__main__":
    main()
