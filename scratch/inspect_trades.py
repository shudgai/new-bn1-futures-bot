import json

with open("data/paper_account.json") as f:
    data = json.load(f)

for trade in reversed(data.get("trades", [])):
    if "龙虾" in trade.get("symbol", "") or "LOBSTER" in trade.get("symbol", "").upper():
        print(json.dumps(trade, indent=2))
        break

for key in data.get("position_meta", {}):
    print("Meta key:", key, data["position_meta"][key])
