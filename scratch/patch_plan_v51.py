import re

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/implementation_plan.md", "r") as f:
    content = f.read()

patch = """
---
### 5. 雙向防禦過濾模組 (Bi-Directional Defense - V5.1 Patch)

**A. 防追空過濾（Anti-Chase Short）：**
*   邊界過濾：禁止在 `Price < LowerBand` 開空。
*   乖離過濾：限制 `Price` 與 `MiddleBand` 距離 `<= 1.5 * ATR`。
*   影線拒絕：過濾 `Lower_Wick > Body * 1.2` 的 K 線。
*   回抽確認：優先在「跌破 -> 回抽 -> 下掉頭」的點位進場。

**B. 防追多過濾（Anti-Chase Long）：**
*   邊界過濾：禁止在 `Price >= UpperBand` 開多。
*   趨勢對齊：檢查長週期均線斜率，若向下則限制多單。
*   過度延伸：連續 3 根大陽線且乖離 `> 1.5 * ATR` 時，進入「只平不開」鎖定。
*   峰值平倉：觸及上軌並出現滯漲（實體縮減/長上影線）時，立即平倉 50%。

**C. 執行優先級：**
1. 環境過濾（寬度、斜率、ATR）。
2. **雙向防禦過濾（邊界、乖離、影線、連續推升）**。
3. 實體K線確認。
4. 速度放緩（Velocity Buffer）觸發限價單。
"""

# Replace the previous section 5 if it exists, otherwise append
if "### 5. 防追空過濾模組" in content:
    content = re.sub(r"### 5\. 防追空過濾模組.*?## Verification Plan", patch + "\n## Verification Plan", content, flags=re.DOTALL)
else:
    content = content.replace("## Verification Plan", patch + "\n## Verification Plan")

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/implementation_plan.md", "w") as f:
    f.write(content)
print("Updated implementation_plan.md for Bi-Directional Defense")
