import re

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/implementation_plan.md", "r") as f:
    content = f.read()

patch = """
---
### 5. 防追空過濾模組 (Anti-Chase Module - V5.1 Patch)
針對趨勢末端的高風險追價行為，新增針對「空單」的硬性防禦機制：
- **邊界保護 (No Chase Outside Bands)**：若價格跌破 `kc_lower`，禁止初次開空。
- **乖離率限制 (Distance Filter)**：`abs(Price - kc_middle) / ATR > 1.5` 時禁止開空。
- **長下影線拒絕 (Rejection Candle Filter)**：下影線大於實體 1.2 倍 (`Lower_Wick / Body_Length > 1.2`) 時取消空單。
- **進場優先級調整**：在 `outer_strategy.py` 的 `aligned_entry` 函式中，於環境過濾後立即插入防追空檢驗，確保不進入弱勢區間。
"""

content = content.replace("## Verification Plan", patch + "\n## Verification Plan")

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/implementation_plan.md", "w") as f:
    f.write(content)
print("Updated implementation_plan.md")
