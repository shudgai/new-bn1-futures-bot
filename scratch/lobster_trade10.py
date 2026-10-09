import json
with open("data/paper_account.json", "r") as f:
    data = json.load(f)
for t in data.get("trades", []):
    if t.get("id") in [1791026289446, 1791026642498]:
        print(f"ID={t['id']} REASON={t.get('reason')} PNL={t.get('pnl')}")
