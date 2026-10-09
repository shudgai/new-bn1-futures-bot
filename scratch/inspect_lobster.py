import json

with open("status.json") as f:
    status = json.load(f)

for pos in status.get("positions", []):
    if "龙虾" in pos.get("symbol", "") or "LOBSTER" in pos.get("symbol", "").upper():
        print(json.dumps(pos, indent=2))
