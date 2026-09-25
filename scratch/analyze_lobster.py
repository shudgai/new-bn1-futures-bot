import json

lines = []
with open("data/paper_account.json", "r") as f:
    for line in f:
        if "龙虾/USDT" in line and "ENTRY_FILTER" in line and "side=SHORT" in line:
            # extract timestamp from bar=...
            try:
                # "text": "[ENTRY_FILTER] 龙虾/USDT side=SHORT bar=1790231280000 reason=..."
                parts = line.split("bar=")
                ts = int(parts[1].split()[0])
                if 1790231200000 <= ts <= 1790231600000:
                    lines.append(line.strip())
            except Exception:
                pass

for l in lines:
    print(l)
