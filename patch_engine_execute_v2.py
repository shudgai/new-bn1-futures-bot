with open("core/engine.py", "r") as f:
    content = f.read()

import re

# Update signature to accept candidate_bar_id
content = re.sub(r"async def _execute_confirmed_channel_break\(self, symbol, frame, price, side, daily_halt=False, v8_reason=None, size_fraction=1\.\):",
                 r"async def _execute_confirmed_channel_break(self, symbol, frame, price, side, daily_halt=False, v8_reason=None, size_fraction=1., candidate_bar_id=None):", content)

content = re.sub(r"candidate_bar_id = closed\.iloc\[-1\]\['timestamp'\]\n\s*reason = v8_reason or \"PURE_TREND_V2\"",
                 r"candidate_bar_id = candidate_bar_id or closed.iloc[-1]['timestamp']\n        reason = v8_reason or \"PURE_TREND_V2\"", content)

with open("core/engine.py", "w") as f:
    f.write(content)
