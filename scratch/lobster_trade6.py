import json
with open("data/paper_account.json", "r") as f:
    data = json.load(f)
for t in data.get("trades", []):
    if t.get("id") in [1791086234557, 1791086995472, 1791085816530, 1791085882298]:
        print(f"{t.get('action')}: TIME={t.get('time')} PRICE={t.get('price')} QTY={t.get('qty')} REASON={t.get('reason')} PNL={t.get('pnl')}")
