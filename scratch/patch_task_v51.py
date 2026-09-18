import re

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/task.md", "r") as f:
    content = f.read()

patch = """
- `[x]` 7. **雙向防禦與動能過濾加固 (Bi-Directional Defense V5.1)**
    - `[x]` `outer_strategy.py` 防追多: 邊界過濾 (`Price >= kc_upper` 拒絕)
    - `[x]` `outer_strategy.py` 防追多: 趨勢對齊 (檢查 `ema_50` 斜率)
    - `[x]` `outer_strategy.py` 防追多: 過度延伸 (連 3 根大陽線且乖離 > 1.5 ATR 拒絕)
    - `[x]` `dual_track_exit_service.py` 峰值平倉: 觸及上軌並出現滯漲時回傳 `EXIT_PEAK_MOMENTUM_PARTIAL`
"""

content += patch

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/task.md", "w") as f:
    f.write(content)
