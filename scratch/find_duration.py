import json

with open("data/paper_account.json") as f:
    data = json.load(f)

# The id in the list actually matches the OPEN trade id! Wait, earlier the open trade had id 1791071408606 and the close trade had id 1791072079133! So IDs are NOT the same!
# The open trade and close trade have DIFFERENT ids because they are order events!
# But wait, how do we match them?
open_trades = [t for t in data.get("trades", []) if t["status"] == "OPEN" and "LOBSTER" in t.get("symbol", "").upper() or "龙虾" in t.get("symbol", "")]
closed_trades = [t for t in data.get("trades", []) if t["status"] == "CLOSED" and "LOBSTER" in t.get("symbol", "").upper() or "龙虾" in t.get("symbol", "")]

# Just print all closed trades that had PNL > 5 or PNL < -10
for ct in closed_trades:
    if ct["side"] == "LONG":
        print(ct["time"], ct["price"], ct["pnl"], ct.get("reason"))

