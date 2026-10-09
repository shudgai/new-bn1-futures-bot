import json

with open("data/paper_account.json", "r") as f:
    data = json.load(f)

for t in data.get("trades", []):
    if "LOBSTER" in t.get("symbol", "").upper() or "龙虾" in t.get("symbol", ""):
        if t.get("side") == "SHORT" and t.get("action") == "OPEN_SHORT":
            print(f"OPEN SHORT: ID={t.get('id')} SYMBOL={t.get('symbol')} TIME={t.get('timestamp_ms')} PRICE={t.get('price')} STATUS={t.get('status')}")
        if t.get("side") == "SHORT" and t.get("action") == "CLOSE_SHORT":
            print(f"CLOSE SHORT: ID={t.get('id')} SYMBOL={t.get('symbol')} REASON={t.get('exit_reason')} PNL={t.get('pnl_usdt')}")
