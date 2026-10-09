import json

with open("data/paper_account.json") as f:
    data = json.load(f)

trades = [t for t in data.get("trades", []) if ("LOBSTER" in t.get("symbol", "").upper() or "龙虾" in t.get("symbol", ""))]

for t in trades[-20:]: # last 20
    if t["side"] == "LONG" and t["status"] == "CLOSED":
        print("Trade ID:", t["id"], "Time:", t["time"], "Entry:", t.get("entry_snapshot", {}).get("quote_price", t["price"]))
        print("Reason:", t.get("reason"))

