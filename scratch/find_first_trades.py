import json

with open("data/paper_account.json") as f:
    data = json.load(f)

trades = [t for t in data.get("trades", []) if ("LOBSTER" in t.get("symbol", "").upper() or "龙虾" in t.get("symbol", ""))]

for t in trades[:10]:
    print("Trade ID:", t["id"], "Side:", t["side"], "Status:", t["status"], "Time:", t["time"])
    if t["status"] == "CLOSED":
        print("Reason:", t.get("reason"))

