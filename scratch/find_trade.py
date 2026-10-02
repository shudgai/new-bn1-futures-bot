import json

with open('data/paper_account.json') as f:
    data = json.load(f)

for trade in data.get('trades', []):
    if trade.get('id') == 1790907026739:
        print(json.dumps(trade, indent=2))
        break
