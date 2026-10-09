import json
import numpy as np

with open("scratch/development_summary.json", "r") as f:
    res = json.load(f)
    
a0_cands = {c["decision_timestamp"]: c for c in res["A0"]["candidates"]}
a_cands = {c["decision_timestamp"]: c for c in res["A"]["candidates"]}

diff = [c for ts, c in a0_cands.items() if ts not in a_cands]

print("RANGE BLOCKED CANDIDATES EXACT METRICS:")
print(f"N = {len(diff)}")
if len(diff) > 0:
    print(f"LONG = {sum(1 for c in diff if c['side'] == 'LONG')}")
    print(f"SHORT = {sum(1 for c in diff if c['side'] == 'SHORT')}")
    print(f"R0.5 = {sum(1 for c in diff if c.get('R0.5'))}")
    print(f"R1.0 = {sum(1 for c in diff if c.get('R1.0'))}")
    print(f"R1.5 = {sum(1 for c in diff if c.get('R1.5'))}")
    print(f"R2.0 = {sum(1 for c in diff if c.get('R2.0'))}")
    for p in [5, 10, 20, 30]:
        print(f"Median {p}m MFE = {np.median([c[f'MFE_{p}m'] for c in diff if c.get(f'MFE_{p}m') is not None]):.2f}")
        print(f"Median {p}m MAE = {np.median([c[f'MAE_{p}m'] for c in diff if c.get(f'MAE_{p}m') is not None]):.2f}")

print("\nKEPT BY RANGE CANDIDATES EXACT METRICS:")
kept = list(a_cands.values())
print(f"N = {len(kept)}")
if len(kept) > 0:
    print(f"LONG = {sum(1 for c in kept if c['side'] == 'LONG')}")
    print(f"SHORT = {sum(1 for c in kept if c['side'] == 'SHORT')}")
    print(f"R0.5 = {sum(1 for c in kept if c.get('R0.5'))}")
    print(f"R1.0 = {sum(1 for c in kept if c.get('R1.0'))}")
    print(f"R1.5 = {sum(1 for c in kept if c.get('R1.5'))}")
    print(f"R2.0 = {sum(1 for c in kept if c.get('R2.0'))}")
    for p in [5, 10, 20, 30]:
        print(f"Median {p}m MFE = {np.median([c[f'MFE_{p}m'] for c in kept if c.get(f'MFE_{p}m') is not None]):.2f}")
        print(f"Median {p}m MAE = {np.median([c[f'MAE_{p}m'] for c in kept if c.get(f'MAE_{p}m') is not None]):.2f}")

