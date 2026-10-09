import json
import pandas as pd
from datetime import datetime, timezone
import math

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

print(f"Matched {len(matched)} trades chronologically.")

df = pd.read_csv("scratch/lobster_real_1m_history.csv")
# Calculate MAs
df['ma3'] = df['close'].rolling(3).mean()
df['ma5'] = df['close'].rolling(5).mean()

# Set index to timestamp for easy slicing
df = df.set_index('timestamp')

results = []
false_positives = []
pinbar_count = 0
strong_body_count = 0
doji_count = 0

for o_t, c_t in matched:
    entry_ms = o_t["id"] # The trade ID is the timestamp in ms roughly
    # Wait, trade id might not be exact entry ms. 'time' is 'MM/DD HH:MM:SS', but 'id' is ms.
    
    # We want to scan bars from entry_ms to close_ms
    close_ms = c_t["id"]
    
    sub_df = df[(df.index >= entry_ms - (60000*10)) & (df.index <= close_ms + (60000*30))].copy()
    if sub_df.empty:
        continue
        
    # Re-extract index for iteration
    sub_df['ts'] = sub_df.index
    sub_df = sub_df.reset_index(drop=True)
    
    side = o_t["side"]
    entry_price = o_t["price"]
    entry_atr = o_t.get("entry_atr", 0.0007)
    if entry_atr <= 0:
        entry_atr = 0.0007 # default if missing
        
    found_exit = False
    
    # Scan bars from entry
    for i in range(5, len(sub_df)):
        ts = sub_df.loc[i, 'ts']
        if ts < entry_ms:
            continue
        if ts > close_ms:
            break
            
        c = sub_df.loc[i, 'close']
        o = sub_df.loc[i, 'open']
        h = sub_df.loc[i, 'high']
        l = sub_df.loc[i, 'low']
        ma3 = sub_df.loc[i, 'ma3']
        ma5 = sub_df.loc[i, 'ma5']
        
        # 4 preceding fully CLOSED bars
        # Are they mature?
        mature = True
        for j in range(i-4, i):
            prev_c = sub_df.loc[j, 'close']
            prev_l = sub_df.loc[j, 'low']
            prev_h = sub_df.loc[j, 'high']
            prev_ma3 = sub_df.loc[j, 'ma3']
            prev_ma5 = sub_df.loc[j, 'ma5']
            
            if side == "LONG":
                if not (prev_l > prev_ma5 or prev_c > prev_ma3):
                    mature = False
                    break
            else:
                if not (prev_h < prev_ma5 or prev_c < prev_ma3):
                    mature = False
                    break
                    
        body = abs(c - o)
        span = h - l
        
        qualified = False
        reason = ""
        
        if side == "LONG":
            upper_wick = h - max(o, c)
            close_pos = (c - l) / span if span > 0 else 0
            body_ratio = body / span if span > 0 else 0
            
            pinbar = (upper_wick > 0.5 * entry_atr) and (upper_wick > 2.0 * body) and (close_pos < 0.40)
            strong_body = (c < o) and (body > 0.5 * entry_atr) and (c < ma3)
            doji = (body_ratio < 0.25) and (c < o) and (c < ma3)
            
            if pinbar or strong_body or doji:
                qualified = True
                if pinbar: reason += "PINBAR "
                if strong_body: reason += "STRONG_BODY "
                if doji: reason += "DOJI "
                
        else: # SHORT
            lower_wick = min(o, c) - l
            close_pos = (c - l) / span if span > 0 else 0
            body_ratio = body / span if span > 0 else 0
            
            pinbar = (lower_wick > 0.5 * entry_atr) and (lower_wick > 2.0 * body) and (close_pos > 0.60)
            strong_body = (c > o) and (body > 0.5 * entry_atr) and (c > ma3)
            doji = (body_ratio < 0.25) and (c > o) and (c > ma3)
            
            if pinbar or strong_body or doji:
                qualified = True
                if pinbar: reason += "PINBAR "
                if strong_body: reason += "STRONG_BODY "
                if doji: reason += "DOJI "
        
        if mature and qualified:
            # Reversal bar!
            # calculate forward excursion
            # We exit at the close of this bar
            exit_price = c
            
            # Find max favorable/adverse in next 5m, 10m, 30m
            def calc_excursion(bars_forward):
                end_idx = min(len(sub_df)-1, i + bars_forward)
                if end_idx == i: return (0, 0)
                forward_prices = sub_df.loc[i+1:end_idx, 'high':'low'] # wait, high and low
                if side == "LONG":
                    mfe = max(forward_prices['low'].min(), exit_price) # wait, MFE for the ORIGINAL trade?
                    # The user wants "relative to the proposed exit price and original side"
                    # For a LONG that just exited, MFE means price goes UP (we missed out)
                    mfe = (forward_prices['high'].max() - exit_price) / entry_atr
                    # MAE means price goes DOWN (we saved ourselves)
                    mae = (exit_price - forward_prices['low'].min()) / entry_atr
                else:
                    mfe = (exit_price - forward_prices['low'].min()) / entry_atr
                    mae = (forward_prices['high'].max() - exit_price) / entry_atr
                return mfe, mae
                
            mfe5, mae5 = calc_excursion(5)
            mfe10, mae10 = calc_excursion(10)
            mfe30, mae30 = calc_excursion(30)
            
            if "PINBAR" in reason: pinbar_count += 1
            if "STRONG_BODY" in reason: strong_body_count += 1
            if "DOJI" in reason: doji_count += 1
            
            # Current exit
            current_exit_price = c_t["price"]
            diff = (current_exit_price - exit_price) / entry_atr if side == "LONG" else (exit_price - current_exit_price) / entry_atr
            
            # HELPFUL: We saved more than we missed out on. (mae > mfe OR current exit is worse)
            # PREMATURE: meaningful favorable continuation (mfe > 2.0 or mfe >> mae and current exit was better)
            
            if diff < -0.5:
                # current exit was worse by > 0.5 ATR
                classification = "HELPFUL"
            elif diff > 1.0:
                # current exit was better by > 1.0 ATR
                classification = "PREMATURE"
            else:
                if mfe30 > 2.0 and mae30 < 1.0:
                    classification = "PREMATURE"
                elif mae30 > 2.0 and mfe30 < 1.0:
                    classification = "HELPFUL"
                else:
                    classification = "NEUTRAL"
                    
            peak_price_before = sub_df.loc[0:i, 'high'].max() if side == "LONG" else sub_df.loc[0:i, 'low'].min()
            peak_gain = (peak_price_before - entry_price) / entry_atr if side == "LONG" else (entry_price - peak_price_before) / entry_atr
            
            results.append({
                "ts": ts,
                "side": side,
                "entry_price": entry_price,
                "entry_atr": entry_atr,
                "peak_gain": peak_gain,
                "bars": i,
                "reason": reason,
                "reversal_price": exit_price,
                "mfe30": mfe30,
                "mae30": mae30,
                "curr_ts": close_ms,
                "curr_price": current_exit_price,
                "curr_reason": c_t.get("reason", ""),
                "diff_atr": diff,
                "class": classification
            })
            found_exit = True
            break
        elif mature and not qualified:
            # Is it a bearish candle for LONG?
            if side == "LONG" and c < o:
                false_positives.append((ts, side, "Ordinary Red", c, ma3))
            elif side == "SHORT" and c > o:
                false_positives.append((ts, side, "Ordinary Green", c, ma3))

print(f"Found {len(results)} proposed exits.")
for r in results:
    dt = datetime.fromtimestamp(r["ts"]/1000, tz=timezone.utc)
    print(f"{dt} {r['side']} | Peak {r['peak_gain']:.2f} ATR | Reason: {r['reason']} | Exit: {r['reversal_price']:.5f}")
    print(f"  Forward 30m -> MFE: {r['mfe30']:.2f} ATR, MAE: {r['mae30']:.2f} ATR")
    print(f"  Current Exit -> {r['curr_price']:.5f} ({r['diff_atr']:.2f} ATR diff) | {r['curr_reason']}")
    print(f"  Class: {r['class']}")
    print("-" * 40)

print(f"Tested {len(false_positives)} ordinary pullbacks (false positives) while mature.")
if false_positives:
    for i in range(min(10, len(false_positives))):
        dt = datetime.fromtimestamp(false_positives[i][0]/1000, tz=timezone.utc)
        print(f"False Positive Test {i}: {dt} {false_positives[i][1]} {false_positives[i][2]} (Close: {false_positives[i][3]:.5f}, MA3: {false_positives[i][4]:.5f}) -> CONTINUED HOLD")

# Positive control lobster case
control_end = 1791071408606 + (60000*12)
control_start = 1791071408606 - (60000*12)
cdf = df[(df.index >= control_start) & (df.index <= control_end)].copy()
cdf['ts'] = cdf.index
cdf = cdf.reset_index(drop=True)
print("=== POSITIVE CONTROL ===")
for i in range(4, len(cdf)):
    if cdf.loc[i, 'ts'] == 1791071820000: # 23:57 UTC
        print("23:57 Bar found. Preceding 4 bars:")
        for j in range(i-4, i):
            dt = datetime.fromtimestamp(cdf.loc[j, 'ts']/1000, tz=timezone.utc)
            low = cdf.loc[j, 'low']
            ma5 = cdf.loc[j, 'ma5']
            c = cdf.loc[j, 'close']
            ma3 = cdf.loc[j, 'ma3']
            print(f"  {dt} | Low {low:.5f} > MA5 {ma5:.5f} ({low>ma5}) | Close {c:.5f} > MA3 {ma3:.5f} ({c>ma3}) | Combined: {low>ma5 or c>ma3}")
        
        print("Reversal Bar (23:57):")
        c = cdf.loc[i, 'close']
        o = cdf.loc[i, 'open']
        h = cdf.loc[i, 'high']
        l = cdf.loc[i, 'low']
        span = h - l
        body = abs(c - o)
        upper = h - max(o, c)
        print(f"  O:{o} H:{h} L:{l} C:{c} Span:{span:.5f} Body:{body:.5f} Upper:{upper:.5f}")
        break

with open("scratch/mature_stats.json", "w") as f:
    json.dump({"pinbar": pinbar_count, "strong": strong_body_count, "doji": doji_count, "total": len(results), "fp": len(false_positives)}, f)
