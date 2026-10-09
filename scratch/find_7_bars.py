import json
import pandas as pd

with open("data/paper_account.json") as f:
    data = json.load(f)

trades = [t for t in data.get("trades", []) if t.get("status") == "CLOSED" and "LOBSTER" in t.get("symbol", "").upper() or "龙虾" in t.get("symbol", "")]

for t in reversed(trades):
    if t["side"] == "LONG" and t["status"] == "CLOSED":
        # check entry and exit time
        # ID is usually timestamp roughly
        # let's find the maximum favorable excursion
        print("Trade ID:", t["id"], "Time:", t["time"], "Entry:", t["price"])
        print("Profit Room Pct:", t.get("profit_room_pct"), "Reason:", t.get("reason"))
        # break after a few
        if "reason" in t:
            # wait, reason is the signal code... wait, reason for CLOSE is in the CLOSED trade!
            pass

