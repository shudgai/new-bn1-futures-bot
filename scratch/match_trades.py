import json

with open("data/paper_account.json") as f:
    data = json.load(f)

trades = [t for t in data.get("trades", []) if ("LOBSTER" in t.get("symbol", "").upper() or "龙虾" in t.get("symbol", ""))]

matched = []
open_t = None
for t in trades:
    if "OPEN" in t["action"]:
        open_t = t
    elif "CLOSE" in t["action"] and open_t is not None:
        if t["side"] == open_t["side"]:
            matched.append((open_t, t))
            open_t = None

print(f"Matched {len(matched)} trades.")
if matched:
    o, c = matched[0]
    print(o["time"], o["price"], "->", c["time"], c["price"])
