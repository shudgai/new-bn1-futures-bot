import json

with open("data/paper_account.json", "r") as f:
    data = json.load(f)

for t in data.get("trades", []):
    if t.get("id") in [1790968085827, 1790968116846]:
        print(json.dumps(t, indent=2))
