import json
import numpy as np

def analyze():
    with open("scratch/development_summary.json", "r") as f:
        results = json.load(f)

    print("=== SUMMARY ===")
    
    for model in ["A0", "A", "B", "C"]:
        cands = results[model]["candidates"]
        counters = results[model]["counters"]
        print(f"\n--- MODEL {model} ---")
        
        if model == "A":
            rb = results[model].get("range_blocked", [])
            print(f"RANGE_BLOCKED: {len(rb)}")
            if rb:
                r05_rb = sum(1 for c in rb if c.get("R0.5"))
                r10_rb = sum(1 for c in rb if c.get("R1.0"))
                print(f"RANGE_BLOCKED R0.5: {r05_rb} / {len(rb)} = {r05_rb/len(rb)*100:.2f}%")
                print(f"RANGE_BLOCKED R1.0: {r10_rb} / {len(rb)} = {r10_rb/len(rb)*100:.2f}%")
                for p in [5, 10, 20, 30]:
                    mfes_rb = [c[f"MFE_{p}m"] for c in rb if f"MFE_{p}m" in c and c[f"MFE_{p}m"] is not None]
                    maes_rb = [c[f"MAE_{p}m"] for c in rb if f"MAE_{p}m" in c and c[f"MAE_{p}m"] is not None]
                    if mfes_rb:
                        print(f"RANGE_BLOCKED Median {p}m MFE: {np.median(mfes_rb):.2f}")
                    if maes_rb:
                        print(f"RANGE_BLOCKED Median {p}m MAE: {np.median(maes_rb):.2f}")
        print(f"Total Candidates: {counters['CANDIDATES']}")
        print(f"LONG: {len([c for c in cands if c['side'] == 'LONG'])}")
        print(f"SHORT: {len([c for c in cands if c['side'] == 'SHORT'])}")
        
        r05 = sum(1 for c in cands if c.get("R0.5"))
        r10 = sum(1 for c in cands if c.get("R1.0"))
        r15 = sum(1 for c in cands if c.get("R1.5"))
        r20 = sum(1 for c in cands if c.get("R2.0"))
        print(f"R0.5: {r05} / {len(cands)} = {r05/len(cands)*100:.2f}%" if len(cands) > 0 else "R0.5: 0")
        print(f"R1.0: {r10} / {len(cands)} = {r10/len(cands)*100:.2f}%" if len(cands) > 0 else "R1.0: 0")
        print(f"R1.5: {r15} / {len(cands)} = {r15/len(cands)*100:.2f}%" if len(cands) > 0 else "R1.5: 0")
        print(f"R2.0: {r20} / {len(cands)} = {r20/len(cands)*100:.2f}%" if len(cands) > 0 else "R2.0: 0")
        
        for p in [5, 10, 20, 30]:
            mfes = [c[f"MFE_{p}m"] for c in cands if f"MFE_{p}m" in c and c[f"MFE_{p}m"] is not None]
            maes = [c[f"MAE_{p}m"] for c in cands if f"MAE_{p}m" in c and c[f"MAE_{p}m"] is not None]
            print(f"Median {p}m MFE: {np.median(mfes):.2f}" if mfes else f"Median {p}m MFE: N/A")
            print(f"Median {p}m MAE: {np.median(maes):.2f}" if maes else f"Median {p}m MAE: N/A")
        
        if model == "B":
            vetoed = counters["ORIGINAL_VETOED"]
            total = counters["CANDIDATES"] + vetoed
            print(f"Vetoed: {vetoed} ({(vetoed/total)*100:.2f}%)" if total > 0 else "Vetoed: 0")
            
        if model == "C":
            print(f"Original Vetoed: {counters['ORIGINAL_VETOED']}")
            print(f"Recovered: {counters['RECOVERED_ENTRIES']}")
            print(f"Cancelled by structure: {counters['CANCELLED_STRUCTURE_BREAK']}")
            print(f"Cancelled by pullback: {counters['CANCELLED_PULLBACK_BREAK']}")
            print(f"Veto Reactivations: {counters['VETO_REACTIVATED']}")
            
        # Chronological stability (5 blocks)
        print("Blocks:")
        # We need to split by index. We have 70700 bars.
        # So each block is roughly 70700 / 5 = 14140.
        # But we only have candidate timestamps.
        # Let's find the time bounds.
        
        # We'll just group candidates roughly by their index if possible, but time is better.
        ts_list = [c["decision_timestamp"] for c in cands]
        if ts_list:
            min_ts = min(ts_list)
            max_ts = max(ts_list)
            block_size = (max_ts - min_ts) / 5
            for i in range(5):
                start = min_ts + i * block_size
                end = min_ts + (i + 1) * block_size
                block_cands = [c for c in cands if start <= c["decision_timestamp"] < end + (1 if i==4 else 0)]
                if len(block_cands) == 0:
                    print(f"Block {i+1}: N=0")
                    continue
                longs = len([c for c in block_cands if c["side"] == "LONG"])
                shorts = len([c for c in block_cands if c["side"] == "SHORT"])
                r05_b = sum(1 for c in block_cands if c.get("R0.5"))
                r10_b = sum(1 for c in block_cands if c.get("R1.0"))
                mfe_20 = np.median([c["MFE_20m"] for c in block_cands if "MFE_20m" in c])
                mae_20 = np.median([c["MAE_20m"] for c in block_cands if "MAE_20m" in c])
                print(f"Block {i+1}: N={len(block_cands)} (L:{longs}/S:{shorts}) R0.5={(r05_b/len(block_cands)):.2%} R1.0={(r10_b/len(block_cands)):.2%} MFE20m={mfe_20:.2f} MAE20m={mae_20:.2f}")

if __name__ == "__main__":
    analyze()
