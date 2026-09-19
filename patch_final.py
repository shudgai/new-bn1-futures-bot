# ── Patch 1: space buffer 0.5 → 1.0 ATR ───────────────────────────
with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()
content = content.replace("    min_space_buffer_atr = 0.5", "    min_space_buffer_atr = 1.0")
with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
print("Patch 1 done: space buffer 0.5->1.0")
