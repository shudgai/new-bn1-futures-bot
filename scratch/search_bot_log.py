import re
import json

targets = ["1791164297760", "1791170817448", "1791157158953", "1791170503324"]
found = {t: [] for t in targets}

with open('bot.log', 'r', encoding='utf-8', errors='ignore') as f:
    for line in f:
        for t in targets:
            if t in line:
                found[t].append(line.strip())
                
for t, lines in found.items():
    print(f"--- {t} ---")
    for l in lines[-10:]: # last 10 occurrences
        print(l)
