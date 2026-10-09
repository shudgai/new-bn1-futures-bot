import sys

with open('core/paper_account.py', 'r') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "qty = float(pos.get(\"qty\") or meta.get(\"qty\") or 0.0)" in line and "notional_value =" in lines[i+1]:
        start = i
        break
        
for i in range(start, len(lines)):
    if "EARLY NET-BE PROFIT PROTECTION" in lines[i]:
        end = i - 1
        break

# Indent lines from start to end by 4 spaces
for i in range(start, end):
    if lines[i].strip():
        lines[i] = "    " + lines[i]

with open('core/paper_account.py', 'w') as f:
    f.writelines(lines)
print(f"Patched lines {start} to {end}")
