import json
from datetime import datetime

with open('data/paper_account.json', 'r') as f:
    data = json.load(f)
    trades = data.get('trades', [])
    
for t in trades[-40:]:
    action = t.get('action')
    if action in ['OPEN_LONG', 'CLOSE_LONG', 'OPEN_SHORT', 'CLOSE_SHORT']:
        dt = datetime.fromtimestamp(t['id']/1000).strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{dt}] {action} {t.get('symbol')} Price={t.get('price')} PnL={t.get('pnl', '')} Reason={t.get('reason', '')}")
