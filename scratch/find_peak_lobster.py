import json

with open("data/paper_account.json") as f:
    data = json.load(f)

for t in reversed(data.get("trades", [])):
    if t.get("status") == "CLOSED" and t.get("side") == "LONG" and ("LOBSTER" in t.get("symbol", "").upper() or "龙虾" in t.get("symbol", "")):
        open_time = next((o["time"] for o in data["trades"] if o["id"] == t["id"] and o["status"] == "OPEN"), "Unknown")
        
        # let's find the peak pnl pct or any info we can get.
        print("Trade ID:", t["id"], "Open Time:", open_time, "Close Time:", t["time"])
        print("Reason:", t.get("reason"), "PNL:", t.get("pnl"), "Peak PNL:", t.get("peak_pnl_pct"))
