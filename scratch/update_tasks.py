import re
path = "/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/task.md"
with open(path, "r") as f:
    content = f.read()

content = content.replace("- `[ ]` 1. **極致點位捕捉 (Velocity Buffer)**", "- `[x]` 1. **極致點位捕捉 (Velocity Buffer)**")
content = content.replace("    - `[ ]` 在 `core/engine.py` 建立 Tick 歷史紀錄與速度計算", "    - `[x]` 在 `core/engine.py` 建立 Tick 歷史紀錄與速度計算")
content = content.replace("    - `[ ]` 實作連續 3 Tick 均速 vs 前 5 Tick 均速的比較", "    - `[x]` 實作連續 3 Tick 均速 vs 前 5 Tick 均速的比較")

content = content.replace("- `[ ]` 3. **動態移動鎖利與回吐空間 (Dynamic Trailing)**", "- `[x]` 3. **動態移動鎖利與回吐空間 (Dynamic Trailing)**")
content = content.replace("    - `[ ]` 修改 `DualTrackExitStrategy` (`core/services/exits/dual_track_exit_service.py`)", "    - `[x]` 修改 `DualTrackExitStrategy` (`core/services/exits/dual_track_exit_service.py`)")
content = content.replace("    - `[ ]` 實作基於斜率係數的 0.2 ATR 移動步長", "    - `[x]` 實作基於斜率係數的 0.2 ATR 移動步長")
content = content.replace("    - `[ ]` 實作 `Total_Drawdown = (0.2 ATR * 斜率係數) + 0.1 ATR` 回撤保護", "    - `[x]` 實作 `Total_Drawdown = (0.2 ATR * 斜率係數) + 0.1 ATR` 回撤保護")

content = content.replace("- `[ ]` 4. **摩擦損耗防禦 (Dynamic Lock Threshold)**", "- `[x]` 4. **摩擦損耗防禦 (Dynamic Lock Threshold)**")
content = content.replace("    - `[ ]` 在 `DualTrackExitStrategy` 將鎖利門檻改為 `Max(0.55 ATR, (部位價值 * fee * 2) + slippage)`", "    - `[x]` 在 `DualTrackExitStrategy` 將鎖利門檻改為 `Max(0.55 ATR, (部位價值 * fee * 2) + slippage)`")

with open(path, "w") as f:
    f.write(content)
