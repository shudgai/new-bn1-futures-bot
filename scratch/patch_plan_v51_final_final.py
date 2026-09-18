import re

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/implementation_plan.md", "r") as f:
    content = f.read()

patch = """
---
### 7. 極致防禦過濾模組 (V5.1 Final Final Update)

**A. 防追空過濾（Anti-Chase Short - 極致防禦）：**
*   **邊界過濾**：若 `Price <= LowerBand` 或 `Prev_Close <= LowerBand` -> **禁止開空**。
*   **反轉過濾**：若 `Close > Open` 或 `Lower_Wick > Body` -> **禁止開空**。
*   **乖離限制**：若 `(kc_middle - Price) / ATR > 1.5` -> **禁止開空**。
*   **回抽機制**：優先在「跌破 -> 回抽中軌 -> 再次下掉頭」的點位進場。

**B. 防追多過濾（Anti-Chase Long - 極致防禦）：**
*   **邊界過濾**：若 `Price >= UpperBand` -> **禁止開多**。
*   **乖離限制**：若 `(Price - kc_middle) / ATR > 1.5` -> **禁止開多**。
*   **趨勢對齊**：檢查 `ema_50` 斜率，若向下則限制多單。
*   **過度延伸**：連續 `>= 2` 根收盤價 `> kc_upper` 且實體 `> 1.2 * ATR` -> **禁止開多**。

**C. 執行優先級：**
1. 環境過濾（寬度、斜率、ATR）。
2. **極致防禦過濾（雙向邊界、雙向乖離、反轉過濾、趨勢對齊）**。
3. 實體K線確認（第二根K線收盤驗證）。
4. 速度放緩（Velocity Buffer）觸發限價單。
"""

# Append to implementation plan
content = content.replace("## Verification Plan", patch + "\n## Verification Plan")

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/implementation_plan.md", "w") as f:
    f.write(content)
print("Updated implementation_plan.md for V5.1 Final Final Update")
