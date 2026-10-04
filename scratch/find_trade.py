import json

with open("data/paper_account.json") as f:
    data = json.load(f)

for t in reversed(data.get("trades", [])):
    if "LOBSTER" in t.get("symbol", "").upper() or "龙虾" in t.get("symbol", ""):
        print(t["id"], t["time"], t["side"], t["price"], t["status"])
