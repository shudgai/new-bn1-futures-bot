import re

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/task.md", "r") as f:
    content = f.read()

content = content.replace("- `[ ]` 6. **防追空防禦模組 (Anti-Chase V5.1)**", "- `[x]` 6. **防追空防禦模組 (Anti-Chase V5.1)**")
content = content.replace("    - `[ ]` `outer_strategy.py` 實作邊界過濾 (`Price < kc_lower` 拒絕)", "    - `[x]` `outer_strategy.py` 實作邊界過濾 (`Price < kc_lower` 拒絕)")
content = content.replace("    - `[ ]` `outer_strategy.py` 實作乖離過濾 (`Distance > 1.5 ATR` 拒絕)", "    - `[x]` `outer_strategy.py` 實作乖離過濾 (`Distance > 1.5 ATR` 拒絕)")
content = content.replace("    - `[ ]` `outer_strategy.py` 實作影線過濾 (`Lower_Wick / Body > 1.2` 拒絕)", "    - `[x]` `outer_strategy.py` 實作影線過濾 (`Lower_Wick / Body > 1.2` 拒絕)")
content = content.replace("    - `[ ]` `outer_strategy.py` 確保在回抽中軌時才啟動 `velocity_slowdown` 進場", "    - `[x]` `outer_strategy.py` 確保在回抽中軌時才啟動 `velocity_slowdown` 進場")

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/task.md", "w") as f:
    f.write(content)
