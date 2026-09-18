import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

content = content.replace(
    'required_cols = {"atr", "ma3", "ma15", "kc_upper", "kc_middle", "kc_lower"}',
    'required_cols = {"atr", "ma3", "ma15", "kc_upper", "kc_lower"}'
)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
