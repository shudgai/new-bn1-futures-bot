import json
from datetime import datetime

with open('data/paper_account.json', 'r') as f:
    data = json.load(f)
    trades = data.get('trades', [])
    
for t in trades[-40:]:
    action = t.get('action')
    if action in ['CLOSE_LONG', 'CLOSE_SHORT']:
        dt = datetime.fromtimestamp(t['id']/1000).strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{dt}] {action} ID={t['id']} {t.get('symbol')}")
        print(f"  Reason: {t.get('reason')}")
        print(f"  Metadata: {json.dumps(t.get('metadata', {}))}")
