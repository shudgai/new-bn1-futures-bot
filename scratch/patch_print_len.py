import re

with open("core/services/symbol_runner.py", "r") as f:
    content = f.read()

content = content.replace(
    'print(f"[{symbol}] {direct_side} Rejected: {reason} | Cols: {list(channel_df.columns)}", flush=True)',
    'print(f"[{symbol}] {direct_side} Rejected: {reason} | Len: {len(channel_df)} | Cols: {list(channel_df.columns)}", flush=True)'
)

with open("core/services/symbol_runner.py", "w") as f:
    f.write(content)
