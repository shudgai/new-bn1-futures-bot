import json
from datetime import datetime

with open('data/paper_account.json', 'r') as f:
    data = json.load(f)

for i, t in enumerate(data.get('trades', [])[:5]):
    tid = t.get('id')
    t_str = t.get('time')
    try:
        dt = datetime.fromtimestamp(tid / 1000.0)
        print(f"ID: {tid} -> Datetime: {dt} | Stored time: {t_str}")
    except Exception as e:
        print(f"Error parsing {tid}: {e}")
