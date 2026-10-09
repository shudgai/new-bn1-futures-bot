import json

with open("data/paper_account.json", "r") as f:
    data = json.load(f)

for t in data.get("trades", []):
    if t.get("action") == "CLOSE_SHORT" and t.get("id") > 1790965000000 and ("LOBSTER" in t.get("symbol").upper() or "龙虾" in t.get("symbol")):
        print(f"CLOSE_SHORT: ID={t['id']} TIME={t['time']} REASON={t.get('reason')} PNL={t.get('pnl')}")
