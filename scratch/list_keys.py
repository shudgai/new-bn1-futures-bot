import json
import glob
for f in glob.glob("data/*.json"):
    try:
        with open(f, "r") as fh:
            d = json.load(fh)
            print(f"File {f} keys: {list(d.keys())}")
            if "history" in d:
                print(f"  History length: {len(d['history'])}")
                if len(d['history']) > 0:
                    print(f"  First history: {d['history'][0]}")
    except Exception as e:
        print(f"File {f} error: {e}")
