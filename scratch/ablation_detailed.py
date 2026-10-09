import json
import pandas as pd
from datetime import datetime, timezone
import numpy as np

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
        
        def eval_excursion(exit_price, forward):
            end_idx = min(len(sub_df)-1, i + forward)
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
            
            mfe5, mae5 = eval_excursion(exit_price, 5)
            mfe10, mae10 = eval_excursion(exit_price, 10)
            mfe20, mae20 = eval_excursion(exit_price, 20)
            mfe30, mae30 = eval_excursion(exit_price, 30)
            cls = classify(mfe30, mae30, diff)
            
            peak_price_before = sub_df.loc[0:i, 'high'].max() if side == "LONG" else sub_df.loc[0:i, 'low'].min()
            peak_gain_atr = (peak_price_before - entry_price) / entry_atr if side == "LONG" else (entry_price - peak_price_before) / entry_atr
            
            full_event = {
                'side': side, 'pinbar': pinbar, 'strong': strong_body, 'doji': doji,
                'diff': diff, 'mfe30': mfe30, 'mae30': mae30, 'class': cls,
                'mfe5': mfe5, 'mae5': mae5, 'mfe10': mfe10, 'mae10': mae10, 'mfe20': mfe20, 'mae20': mae20,
                'ts': ts, 'entry_price': entry_price, 'entry_atr': entry_atr,
                'curr_exit_price': curr_exit_price, 'curr_exit_time': close_ms,
                'curr_reason': c_t.get("reason", ""),
                'exit_price': exit_price, 'peak_gain_atr': peak_gain_atr,
                'event_id': f"{side}_{ts}",
                'bars_since_entry': i
            }
            
        if mature and (pinbar or doji) and no_sb_event is None:
            exit_price = c
            curr_exit_price = c_t["price"]
            diff = (curr_exit_price - exit_price) / entry_atr if side == "LONG" else (exit_price - curr_exit_price) / entry_atr
            
            mfe5, mae5 = eval_excursion(exit_price, 5)
            mfe10, mae10 = eval_excursion(exit_price, 10)
            mfe20, mae20 = eval_excursion(exit_price, 20)
            mfe30, mae30 = eval_excursion(exit_price, 30)
            cls = classify(mfe30, mae30, diff)
            
            peak_price_before = sub_df.loc[0:i, 'high'].max() if side == "LONG" else sub_df.loc[0:i, 'low'].min()
            peak_gain_atr = (peak_price_before - entry_price) / entry_atr if side == "LONG" else (entry_price - peak_price_before) / entry_atr
            
            no_sb_event = {
                'side': side, 'pinbar': pinbar, 'strong': strong_body, 'doji': doji,
                'diff': diff, 'mfe30': mfe30, 'mae30': mae30, 'class': cls,
                'mfe5': mfe5, 'mae5': mae5, 'mfe10': mfe10, 'mae10': mae10, 'mfe20': mfe20, 'mae20': mae20,
                'ts': ts, 'entry_price': entry_price, 'entry_atr': entry_atr,
                'curr_exit_price': curr_exit_price, 'curr_exit_time': close_ms,
                'curr_reason': c_t.get("reason", ""),
                'exit_price': exit_price, 'peak_gain_atr': peak_gain_atr,
                'event_id': f"{side}_{ts}",
                'bars_since_entry': i
            }
            
        if full_event and no_sb_event:
            break
            
    if full_event: full_events.append(full_event)
    if no_sb_event: no_sb_events.append(no_sb_event)

def print_stats(events, name):
    print(f"\n=== {name} STATISTICS ===")
    print(f"N = {len(events)}")
    diffs = [-e['diff'] for e in events] # Advantage is negative of diff
    if diffs:
        print(f"MEAN exit advantage ATR = {np.mean(diffs):.2f}")
        print(f"P25 = {np.percentile(diffs, 25):.2f}")
        print(f"P50 / MEDIAN = {np.median(diffs):.2f}")
        print(f"P75 = {np.percentile(diffs, 75):.2f}")
        print(f"MIN = {np.min(diffs):.2f}")
        print(f"MAX = {np.max(diffs):.2f}")

print_stats(full_events, "FULL")
print_stats(no_sb_events, "NO_STRONG_BODY")

print("\n=== COMPLETE OVERLAP ACCOUNTING ===")
p_only = sum(1 for e in full_events if e['pinbar'] and not e['strong'] and not e['doji'])
d_only = sum(1 for e in full_events if e['doji'] and not e['strong'] and not e['pinbar'])
s_only = sum(1 for e in full_events if e['strong'] and not e['pinbar'] and not e['doji'])
pd_only = sum(1 for e in full_events if e['pinbar'] and e['doji'] and not e['strong'])
ps_only = sum(1 for e in full_events if e['pinbar'] and e['strong'] and not e['doji'])
ds_only = sum(1 for e in full_events if e['doji'] and e['strong'] and not e['pinbar'])
all3 = sum(1 for e in full_events if e['pinbar'] and e['doji'] and e['strong'])

print(f"PINBAR_ONLY = {p_only}")
print(f"DOJI_ONLY = {d_only}")
print(f"STRONG_BODY_ONLY = {s_only}")
print(f"PINBAR_DOJI_ONLY = {pd_only}")
print(f"PINBAR_STRONG_ONLY = {ps_only}")
print(f"DOJI_STRONG_ONLY = {ds_only}")
print(f"ALL_THREE = {all3}")

total_sum = p_only + d_only + s_only + pd_only + ps_only + ds_only + all3
print(f"\nsum(categories) = {total_sum}")
print(f"FULL_UNIQUE_EVENTS = {len(full_events)}")

print(f"\nunique(PINBAR OR DOJI) = {len(no_sb_events)}")
print("Event IDs forming the NO_STRONG_BODY set:")
for e in no_sb_events:
    print(f" - {e['event_id']}")

print("\n=== PRINT ALL 8 CANDIDATE EVENTS ===")
for e in no_sb_events:
    dt = datetime.fromtimestamp(e['ts']/1000, tz=timezone.utc)
    sys_dt = datetime.fromtimestamp(e['curr_exit_time']/1000, tz=timezone.utc)
    adv = -e['diff']
    print(f"event_id: {e['event_id']}")
    print(f"timestamp: {e['ts']} ({dt})")
    print(f"side: {e['side']}")
    print(f"PINBAR: {e['pinbar']} | DOJI: {e['doji']}")
    print(f"entry_price: {e['entry_price']:.5f}")
    print(f"frozen_entry_ATR: {e['entry_atr']:.5f}")
    print(f"peak_gain_ATR: {e['peak_gain_atr']:.2f}")
    print(f"bars_since_entry: {e['bars_since_entry']}")
    print(f"proposed_exit_price: {e['exit_price']:.5f}")
    print(f"current_system_exit_timestamp: {e['curr_exit_time']} ({sys_dt})")
    print(f"current_system_exit_price: {e['curr_exit_price']:.5f}")
    print(f"current_system_exit_reason: {e['curr_reason']}")
    print(f"exit_advantage_ATR: {adv:.2f}")
    print(f"forward 5m  -> MFE: {e['mfe5']:.2f} ATR | MAE: {e['mae5']:.2f} ATR")
    print(f"forward 10m -> MFE: {e['mfe10']:.2f} ATR | MAE: {e['mae10']:.2f} ATR")
    print(f"forward 20m -> MFE: {e['mfe20']:.2f} ATR | MAE: {e['mae20']:.2f} ATR")
    print(f"forward 30m -> MFE: {e['mfe30']:.2f} ATR | MAE: {e['mae30']:.2f} ATR")
    print(f"classification: {e['class']}")
    print("-" * 40)

print("\n=== INSPECT THE ONE PREMATURE EVENT ===")
for e in no_sb_events:
    if e['class'] == 'PREMATURE':
        print(f"Premature Event ID: {e['event_id']}")
        # Get bars before and after
        sub_df = df[(df.index >= e['ts'] - (60000*5)) & (df.index <= e['ts'] + (60000*5))]
        print("Maturity four bars & Reversal bar & Continuation:")
        for ts, row in sub_df.iterrows():
            dt_s = datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime("%H:%M:%S")
            label = "Reversal" if ts == e['ts'] else ("Before" if ts < e['ts'] else "After")
            print(f"[{label}] {dt_s} O:{row['open']} H:{row['high']} L:{row['low']} C:{row['close']} MA3:{row['ma3']:.5f} MA5:{row['ma5']:.5f}")
        adv = -e['diff']
        print(f"Maximum opportunity cost: {-adv:.2f} ATR")
        print(f"Actual eventual exit price: {e['curr_exit_price']:.5f} at {e['curr_exit_time']}")
        print("Reason:", e['curr_reason'])
