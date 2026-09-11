#!/usr/bin/env python3
import json
import os
import re

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
TARGET_FILES = ['paper_account.json', 'testnet_account.json']

pattern_paren = re.compile(r'Mandatory_Fail:\s*[A-Za-z0-9_]+\(([^)]*)\)')
pattern_key = re.compile(r'Mandatory_Fail:\s*([A-Za-z0-9_]+)')

changed_files = []

for fname in TARGET_FILES:
    path = os.path.join(DATA_DIR, fname)
    if not os.path.exists(path):
        continue
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    def rewrite(obj):
        if isinstance(obj, str):
            m = pattern_paren.search(obj)
            if m:
                obj = pattern_paren.sub(lambda mo: mo.group(1), obj)
            else:
                if 'Mandatory_Fail:' in obj:
                    obj = pattern_key.sub(lambda mo: mo.group(1).replace('_', ' '), obj)
            return obj
        elif isinstance(obj, dict):
            return {k: rewrite(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [rewrite(v) for v in obj]
        else:
            return obj

    new_data = rewrite(data)
    if new_data != data:
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
        os.replace(path + '.tmp', path)
        changed_files.append(path)

if changed_files:
    print('Updated:', '\n'.join(changed_files))
else:
    print('No changes')
