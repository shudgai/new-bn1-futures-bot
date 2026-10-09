import json
with open("data/paper_account.json", "r") as f:
    data = json.load(f)
for t in data.get("trades", []):
    if t.get("id") in [1791086995472, 1791080825094]:
        print(f"ID={t['id']} peak_pct={t.get('peak_pnl_pct')} pnl={t.get('pnl')}")
