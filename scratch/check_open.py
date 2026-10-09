import json
with open('data/paper_account.json', 'r') as f:
    state = json.load(f)
pos = state.get('positions', {})
if isinstance(pos, dict):
    p = pos.get('龙虾/USDT')
    print(f"POSITION: {p}")
elif isinstance(pos, list):
    for p in pos:
        if p.get('symbol') == '龙虾/USDT':
            print(f"POSITION: {p}")
