import json
with open('data/paper_account.json') as f:
    acc = json.load(f)
trade = next((t for t in acc.get('trades', []) if str(t.get('id')) == '1790968085827'), None)
if trade: print(json.dumps(trade, indent=2))
