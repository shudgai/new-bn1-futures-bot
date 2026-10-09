import json
with open("data/paper_account.json", "r") as f:
    data = json.load(f)
for t in data.get("trades", []):
    if t.get("id") in [1791086234557, 1791086995472]:
        print(json.dumps(t, indent=2))
