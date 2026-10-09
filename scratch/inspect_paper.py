import json

try:
    with open("data/paper_account.json") as f:
        data = json.load(f)
    print("Paper Account Data keys:", data.keys())
    for pos in data.get("positions", []):
        print(json.dumps(pos, indent=2))
except Exception as e:
    print(e)
