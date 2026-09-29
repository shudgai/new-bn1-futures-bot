with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if "物理收盤校驗：非收盤 K 棒，0.1秒都不准偷跑" in line:
        skip = True
    if skip and "return wait" in line and "REJECT_UNCLOSED_BAR" in line:
        skip = False
        continue
    if not skip:
        new_lines.append(line)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.writelines(new_lines)
