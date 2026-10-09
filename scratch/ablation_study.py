import json
import pandas as pd
from datetime import datetime, timezone

with open("data/paper_account.json") as f:
    data = json.load(f)

# Sort trades chronologically by id
all_trades = sorted(data.get("trades", []), key=lambda x: x["id"])
trades = [t for t in all_trades if "LOBSTER" in t.get("symbol", "").upper() or "龙虾" in t.get("symbol", "")]

matched = []
open_t = None
for t in trades:
    if "OPEN" in t["action"]:
        open_t = t
    elif "CLOSE" in t["action"] and open_t is not None:
        if t["side"] == open_t["side"]:
            matched.append((open_t, t))
            open_t = None

df = pd.read_csv("scratch/lobster_real_1m_history.csv")
df['ma3'] = df['close'].rolling(3).mean()
df['ma5'] = df['close'].rolling(5).mean()
df = df.set_index('timestamp')

full_events = []
no_sb_events = []

for o_t, c_t in matched:
    entry_ms = o_t["id"]
    close_ms = c_t["id"]
    
    sub_df = df[(df.index >= entry_ms - (60000*10)) & (df.index <= close_ms + (60000*30))].copy()
    if sub_df.empty: continue
        
    sub_df['ts'] = sub_df.index
    sub_df = sub_df.reset_index(drop=True)
    
    side = o_t["side"]
    entry_price = o_t["price"]
    entry_atr = o_t.get("entry_atr", 0.0007)
    if entry_atr <= 0: entry_atr = 0.0007
        
    # Find full model event and no_sb event
    full_event = None
    no_sb_event = None
    
    for i in range(5, len(sub_df)):
        ts = sub_df.loc[i, 'ts']
        if ts < entry_ms: continue
        if ts > close_ms: break
            
        c, o, h, l = sub_df.loc[i, 'close'], sub_df.loc[i, 'open'], sub_df.loc[i, 'high'], sub_df.loc[i, 'low']
        ma3, ma5 = sub_df.loc[i, 'ma3'], sub_df.loc[i, 'ma5']
        
        mature = True
        for j in range(i-4, i):
            p_c, p_l, p_h = sub_df.loc[j, 'close'], sub_df.loc[j, 'low'], sub_df.loc[j, 'high']
            p_ma3, p_ma5 = sub_df.loc[j, 'ma3'], sub_df.loc[j, 'ma5']
            
            if side == "LONG":
                if not (p_l > p_ma5 or p_c > p_ma3):
                    mature = False; break
            else:
                if not (p_h < p_ma5 or p_c < p_ma3):
                    mature = False; break
                    
        body = abs(c - o)
        span = h - l
        
        pinbar = False
        strong_body = False
        doji = False
        
        if side == "LONG":
            upper_wick = h - max(o, c)
            close_pos = (c - l) / span if span > 0 else 0
            body_ratio = body / span if span > 0 else 0
            pinbar = (upper_wick > 0.5 * entry_atr) and (upper_wick > 2.0 * body) and (close_pos < 0.40)
            strong_body = (c < o) and (body > 0.5 * entry_atr) and (c < ma3)
            doji = (body_ratio < 0.25) and (c < o) and (c < ma3)
        else:
            lower_wick = min(o, c) - l
            close_pos = (c - l) / span if span > 0 else 0
            body_ratio = body / span if span > 0 else 0
            pinbar = (lower_wick > 0.5 * entry_atr) and (lower_wick > 2.0 * body) and (close_pos > 0.60)
            strong_body = (c > o) and (body > 0.5 * entry_atr) and (c > ma3)
            doji = (body_ratio < 0.25) and (c > o) and (c > ma3)
        
        def eval_excursion(exit_price):
            end_idx = min(len(sub_df)-1, i + 30)
            if end_idx == i: return 0, 0
            fp = sub_df.loc[i+1:end_idx, 'high':'low']
            if side == "LONG":
                mfe = (fp['high'].max() - exit_price) / entry_atr
                mae = (exit_price - fp['low'].min()) / entry_atr
            else:
                mfe = (exit_price - fp['low'].min()) / entry_atr
                mae = (fp['high'].max() - exit_price) / entry_atr
            return mfe, mae
            
        def classify(mfe30, mae30, diff):
            if diff < -0.5: return "HELPFUL"
            elif diff > 1.0: return "PREMATURE"
            else:
                if mfe30 > 2.0 and mae30 < 1.0: return "PREMATURE"
                elif mae30 > 2.0 and mfe30 < 1.0: return "HELPFUL"
                else: return "NEUTRAL"
        
        if mature and (pinbar or strong_body or doji) and full_event is None:
            exit_price = c
            curr_exit_price = c_t["price"]
            diff = (curr_exit_price - exit_price) / entry_atr if side == "LONG" else (exit_price - curr_exit_price) / entry_atr
            mfe30, mae30 = eval_excursion(exit_price)
            cls = classify(mfe30, mae30, diff)
            full_event = {
                'side': side, 'pinbar': pinbar, 'strong': strong_body, 'doji': doji,
                'diff': diff, 'mfe30': mfe30, 'mae30': mae30, 'class': cls
            }
            
        if mature and (pinbar or doji) and no_sb_event is None:
            exit_price = c
            curr_exit_price = c_t["price"]
            diff = (curr_exit_price - exit_price) / entry_atr if side == "LONG" else (exit_price - curr_exit_price) / entry_atr
            mfe30, mae30 = eval_excursion(exit_price)
            cls = classify(mfe30, mae30, diff)
            no_sb_event = {
                'side': side, 'pinbar': pinbar, 'doji': doji,
                'diff': diff, 'mfe30': mfe30, 'mae30': mae30, 'class': cls
            }
            
        if full_event and no_sb_event:
            break
            
    if full_event: full_events.append(full_event)
    if no_sb_event: no_sb_events.append(no_sb_event)

import numpy as np

def summary(events, name):
    print(f"=== {name} ===")
    print("TOTAL UNIQUE EVENTS =", len(events))
    longs = sum(1 for e in events if e['side'] == 'LONG')
    shorts = sum(1 for e in events if e['side'] == 'SHORT')
    print("LONG =", longs, "SHORT =", shorts)
    
    helpful = sum(1 for e in events if e['class'] == 'HELPFUL')
    neutral = sum(1 for e in events if e['class'] == 'NEUTRAL')
    premature = sum(1 for e in events if e['class'] == 'PREMATURE')
    total = len(events)
    
    if total > 0:
        print(f"HELPFUL = {helpful} ({helpful/total*100:.1f}%)")
        print(f"NEUTRAL = {neutral} ({neutral/total*100:.1f}%)")
        print(f"PREMATURE = {premature} ({premature/total*100:.1f}%)")
        
        diffs = [-e['diff'] for e in events] # Advantage ATR is negative of diff (since diff is curr - proposed, if curr is worse, advantage is positive. Wait, diff was (curr_exit_price - proposed)/atr. If proposed exit price was HIGHER for LONG, diff is negative. So -diff is the advantage in ATR!)
        diffs = np.array(diffs)
        print(f"Median Advantage ATR = {np.median(diffs):.2f}")
        print(f"Mean Advantage ATR = {np.mean(diffs):.2f}")
        print(f"P25/P50/P75 = {np.percentile(diffs, 25):.2f} / {np.median(diffs):.2f} / {np.percentile(diffs, 75):.2f}")
        
        premature_costs = []
        for e in events:
            if e['class'] == 'PREMATURE':
                premature_costs.append(e['diff']) # If premature, diff is positive (current was better)
                
        worst = max(premature_costs) if premature_costs else 0
        print(f"Worst Premature Cost ATR = {worst:.2f}")
    
    if name == "FULL":
        print("OVERLAP MATRIX:")
        p_only = sum(1 for e in events if e['pinbar'] and not e['strong'] and not e['doji'])
        d_only = sum(1 for e in events if e['doji'] and not e['strong'] and not e['pinbar'])
        s_only = sum(1 for e in events if e['strong'] and not e['pinbar'] and not e['doji'])
        pd = sum(1 for e in events if e['pinbar'] and e['doji'] and not e['strong'])
        ps = sum(1 for e in events if e['pinbar'] and e['strong'] and not e['doji'])
        ds = sum(1 for e in events if e['doji'] and e['strong'] and not e['pinbar'])
        all3 = sum(1 for e in events if e['pinbar'] and e['doji'] and e['strong'])
        print(f"PINBAR only = {p_only}")
        print(f"DOJI only = {d_only}")
        print(f"STRONG_BODY only = {s_only}")
        print(f"PINBAR + DOJI = {pd}")
        print(f"PINBAR + STRONG_BODY = {ps}")
        print(f"DOJI + STRONG_BODY = {ds}")
        print(f"ALL THREE = {all3}")

summary(full_events, "FULL")
summary(no_sb_events, "NO_STRONG_BODY")

# Control
control_start = 1791071408606 - (60000*12)
control_end = 1791071408606 + (60000*12)
cdf = df[(df.index >= control_start) & (df.index <= control_end)].copy()
cdf['ts'] = cdf.index
cdf = cdf.reset_index(drop=True)

print("=== LOBSTER ALIGNMENT FIX ===")
# Find the reversal bar (23:57:00 is 1791071820000 UTC ms)
for i in range(4, len(cdf)):
    if cdf.loc[i, 'ts'] == 1791071820000: # 23:57
        print("CSV Timestamp | Open Time | Close Time | O | H | L | C | MA3 | MA5")
        for j in range(i-4, i+1):
            row = cdf.loc[j]
            ts = row['ts']
            open_dt = datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime("%H:%M:%S")
            close_dt = datetime.fromtimestamp((ts+59999)/1000, tz=timezone.utc).strftime("%H:%M:%S")
            print(f"{ts} | {open_dt} | {close_dt} | {row['open']:.5f} / {row['high']:.5f} / {row['low']:.5f} / {row['close']:.5f} | {row['ma3']:.5f} | {row['ma5']:.5f}")
        
        print("\nMaturity Boolean Expressions:")
        for j in range(i-4, i):
            l = cdf.loc[j, 'low']
            c = cdf.loc[j, 'close']
            ma5 = cdf.loc[j, 'ma5']
            ma3 = cdf.loc[j, 'ma3']
            print(f"Bar {j - i}: (Low {l:.5f} > MA5 {ma5:.5f}) OR (Close {c:.5f} > MA3 {ma3:.5f}) -> {l > ma5} OR {c > ma3} -> {l > ma5 or c > ma3}")
            
        
        # Test rules
        entry_atr = 0.000738
        c, o, h, l = cdf.loc[i, 'close'], cdf.loc[i, 'open'], cdf.loc[i, 'high'], cdf.loc[i, 'low']
        span = h - l
        body = abs(c - o)
        upper_wick = h - max(o, c)
        close_pos = (c - l) / span if span > 0 else 0
        body_ratio = body / span if span > 0 else 0
        ma3 = cdf.loc[i, 'ma3']
        
        pinbar = (upper_wick > 0.5 * entry_atr) and (upper_wick > 2.0 * body) and (close_pos < 0.40)
        strong_body = (c < o) and (body > 0.5 * entry_atr) and (c < ma3)
        doji = (body_ratio < 0.25) and (c < o) and (c < ma3)
        
        print("LOBSTER REVERSAL COMPONENTS:")
        print(f"PINBAR = {pinbar}")
        print(f"STRONG_BODY = {strong_body}")
        print(f"DOJI = {doji}")
        print(f"FULL_EXIT = {pinbar or strong_body or doji}")
        print(f"NO_STRONG_BODY_EXIT = {pinbar or doji}")
        break
