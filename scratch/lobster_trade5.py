import json
with open("data/paper_account.json", "r") as f:
    data = json.load(f)
for t in data.get("trades", []):
    if t.get("id") == 1791080825094 or t.get("id") == 1791080487139:
        print(f"{t.get('action')}: TIME={t.get('time')} PRICE={t.get('price')} QTY={t.get('qty')} REASON={t.get('reason')} PNL={t.get('pnl')}")
