import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

content = content.replace(
    'kc_mid = latest["kc_middle"]',
    'kc_mid = latest.get("kc_middle", latest.get("ema_20", latest.get("kc_upper") - latest.get("atr") * 1.5))'
)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
