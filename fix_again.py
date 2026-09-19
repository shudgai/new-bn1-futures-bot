with open('tests/test_v9_core_logic.py', 'r') as f:
    content = f.read()

content = content.replace("[SPECIAL_ENTRY] Extreme Impulse LONG (>=2.0 ATR)", "[SPECIAL_ENTRY] Overextended Impulse LONG (Limit Order)")

with open('tests/test_v9_core_logic.py', 'w') as f:
    f.write(content)
