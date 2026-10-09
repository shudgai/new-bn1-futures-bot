import json
with open("data/paper_account.json", "r") as f:
    data = json.load(f)
for t in data.get("trades", []):
    if t.get("action") == "CLOSE_SHORT" and ("LOBSTER" in t.get("symbol").upper() or "龙虾" in t.get("symbol")):
        peak_pct = t.get("peak_pnl_pct", 0) or 0
        qty = t.get("qty", 0)
        entry_price = 0
        # find matching open to get entry price accurately or calculate from amount
        for o in data.get("trades", []):
            if o.get("action") == "OPEN_SHORT" and o.get("id") < t.get("id") and o.get("symbol") == t.get("symbol"):
                entry_price = o.get("price")
        
        peak_pnl = 0
        if peak_pct > 0 and entry_price > 0:
            peak_pnl = peak_pct * entry_price * qty
            if peak_pnl > 10:
                print(f"Trade Exit ID={t['id']} Time={t['time']} Peak Net PNL est={peak_pnl:.2f} Final PNL={t.get('pnl')}")
