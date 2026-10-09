import json
with open("data/paper_account.json") as f:
    data = json.load(f)
for t in data.get("history", []):
    if "龙虾" in t.get("symbol", "") or "NEIRO" in t.get("symbol", "").upper():
        print(f"Trade: side={t.get('side')} symbol={t.get('symbol')} entry_time={t.get('entry_time')} exit_time={t.get('exit_time')}")
for k, t in data.get("positions", {}).items():
    if "龙虾" in t.get("symbol", "") or "NEIRO" in t.get("symbol", "").upper():
        print(f"Position: side={t.get('side')} symbol={t.get('symbol')} entry_time={t.get('entry_time')}")
