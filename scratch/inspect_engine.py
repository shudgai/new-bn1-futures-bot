import json

try:
    with open("data/paper_account.json") as f:
        data = json.load(f)
    print("Positions type:", type(data.get("positions")))
    print("Positions:", data.get("positions"))
except Exception as e:
    print(e)
