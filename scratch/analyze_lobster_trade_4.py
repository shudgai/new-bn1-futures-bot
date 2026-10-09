import json
with open("data/paper_account.json") as f:
    data = json.load(f)
for t in data.get("trades", []):
    if "龙虾" in t.get("symbol", "") or "NEIRO" in t.get("symbol", "").upper() or "LOBSTER" in t.get("symbol", "").upper():
        if t.get("side") == "SHORT":
            print(f"SHORT: {t}")
for k, t in data.get("positions", {}).items():
    if "龙虾" in t.get("symbol", "") or "NEIRO" in t.get("symbol", "").upper() or "LOBSTER" in t.get("symbol", "").upper():
        if t.get("side") == "SHORT":
            print(f"SHORT POS: {t}")
