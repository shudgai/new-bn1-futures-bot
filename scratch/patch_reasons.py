import re

with open("core/engine.py", "r") as f:
    content = f.read()

old_reasons = """
                "KC_LIVE_UPPER_BREAK_LONG", "KC_LIVE_LOWER_BREAK_SHORT",
                "KC_LIVE_UPPER_MOMENTUM_LONG",
                "LIVE_UPPER_BREAKOUT", "LIVE_LOWER_BREAKOUT",
                "KC_UPPER_BREAKOUT_STRICT", "KC_LOWER_BREAKOUT_STRICT",
"""

new_reasons = """
                "KC_LIVE_UPPER_BREAK_LONG", "KC_LIVE_LOWER_BREAK_SHORT",
                "KC_LIVE_UPPER_MOMENTUM_LONG",
                "LIVE_UPPER_BREAKOUT_ABNORMAL", "LIVE_LOWER_BREAKOUT_ABNORMAL",
                "KC_UPPER_BREAKOUT_STRICT", "KC_LOWER_BREAKOUT_STRICT",
"""

if old_reasons.strip() in content:
    content = content.replace(old_reasons.strip(), new_reasons.strip())
    with open("core/engine.py", "w") as f:
        f.write(content)
    print("Replaced reasons successfully")
else:
    print("Could not find old reasons")
