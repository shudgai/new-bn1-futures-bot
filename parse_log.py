import json

with open('data/paper_account.json', 'r') as f:
    data = json.load(f)

for trade in reversed(data['history']):
    if trade.get('action') == 'CLOSE_LONG' and '15:14:21' in trade.get('time', ''):
        print(json.dumps(trade, indent=2))
        break
