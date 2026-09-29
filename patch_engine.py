with open("core/engine.py", "r") as f:
    content = f.read()

import re

# Remove RULE_CODES check from `_place_structured_entry_locked`
content = re.sub(r"from core\.services\.strategies\.unified_entry_strategy import RULE_CODES, evaluate_closed_entry, had_close\n\s*if \(symbol not in DEFAULT_SYMBOLS or signal\.get\('entry_mode'\) != 'CHANNEL_SWING'\n\s*or signal\.get\('signal_code'\) not in RULE_CODES\):",
'''
        if (symbol not in DEFAULT_SYMBOLS or signal.get('entry_mode') != 'CHANNEL_SWING'):
''', content, flags=re.DOTALL)

with open("core/engine.py", "w") as f:
    f.write(content)
