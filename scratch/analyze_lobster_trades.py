import json
import glob
for f in glob.glob("data/*.json"):
    try:
        with open(f, "r") as fh:
            data = json.load(fh)
            trades = data.get("history", [])
            for t in trades:
                if t.get("side") == "SHORT" and ("龙虾" in t.get("symbol", "") or "LOBSTER" in t.get("symbol", "").upper() or "NEIRO" in t.get("symbol", "").upper()):
                    print(f"File: {f} Trade: {t}")
    except Exception as e:
        pass
