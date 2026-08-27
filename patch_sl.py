import re

with open("core/engine.py", "r") as f:
    content = f.read()

# For LONG
old_long = "max_sl = target_price - (2.0 * atr)"
new_long = "max_sl = max(target_price - (2.0 * atr), target_price * (1.0 - config.FIXED_STOP_LOSS_PCT))"

# For SHORT
old_short = "max_sl = target_price + (2.0 * atr)"
new_short = "max_sl = min(target_price + (2.0 * atr), target_price * (1.0 + config.FIXED_STOP_LOSS_PCT))"

content = content.replace(old_long, new_long)
content = content.replace(old_short, new_short)

with open("core/engine.py", "w") as f:
    f.write(content)
