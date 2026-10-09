import json

with open("scratch/development_summary.json") as f:
    results = json.load(f)

for m in ["A0", "A", "B", "C"]:
    cands = results[m]["candidates"]
    counters = results[m]["counters"]
    print(f"Model {m}: CANDIDATES={counters['CANDIDATES']} EVALUATED={len(cands)}")
