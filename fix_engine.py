import re

with open("core/engine.py", "r") as f:
    content = f.read()

# Fix first_reversal_pivot logic to require some age
replacement = """
                        age_sec = max(0.0, time.time() - float(position.get("open_timestamp") or time.time()))
                        first_reversal_pivot = bool(
                            age_sec > 180 and
                            ((position.get("side") == "LONG" and live_close < live_open and prior_high - live_close >= fast_threshold)
                            or (position.get("side") == "SHORT" and live_close > live_open and live_close - prior_low >= fast_threshold))
                        )
"""

content = re.sub(
    r'first_reversal_pivot = bool\(\s*\(\s*position\.get\("side"\) == "LONG" and live_close < live_open and prior_high - live_close >= fast_threshold\s*\)\s*or\s*\(\s*position\.get\("side"\) == "SHORT" and live_close > live_open and live_close - prior_low >= fast_threshold\s*\)\s*\)',
    replacement.strip(),
    content
)

with open("core/engine.py", "w") as f:
    f.write(content)
