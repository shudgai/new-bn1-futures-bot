import json
import glob
for f in glob.glob("data/*.json"):
    try:
        with open(f, "r") as fh:
            data = json.load(fh)
            for t in data.get("history", []):
                if t.get("side") == "SHORT":
                    print(f"History - File: {f} Symbol: {t.get('symbol')} Time: {t.get('entry_time')} Exit: {t.get('exit_time')}")
            for k, t in data.get("positions", {}).items():
                if t.get("side") == "SHORT":
                    print(f"Position - File: {f} Symbol: {t.get('symbol')} Time: {t.get('entry_time')}")
    except Exception as e:
        pass
