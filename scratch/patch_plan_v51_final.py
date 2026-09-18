import re

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/implementation_plan.md", "r") as f:
    content = f.read()

patch = """
---
### 6. 雙重確認狀態機機制 (Two-Candle Confirmation State Machine - V5.1 Final)

為了徹底封鎖「破軌瞬間追價」的風險，系統將從無狀態的單次 K 線判定，改為帶有狀態機的雙重確認流程：

**A. 狀態管理 (`breakout_alert_active`)**：
*   **K1 (破軌警報)**：價格觸碰或突破 CK 上/下軌，不發送進場訊號，而是記錄 `breakout_alert_active = True` 並鎖定突破方向。
*   **K2 (驗證決策)**：K2 收盤後，若 `breakout_alert_active` 為真，則執行：
    1.  **實體驗證**：要求同色實體且 `SOLID_BODY_RATIO >= 0.50`。
    2.  **雙向防禦過濾**（邊界、乖離、趨勢斜率、長影線）。
    3.  若全數通過，才放行進場訊號。
*   **K3 (延遲確認)**：若 K2 實體驗證未達標（弱體或短暫反向），保留警報狀態至 K3 進行最終驗證。若 K3 亦失敗，則重置狀態。

**B. 實作位置**：
*   修改 `core/services/strategies/outer_strategy.py` 內的 `aligned_entry`。由於該函式目前為靜態/無狀態設計，我們將把 `breakout_alert` 狀態掛載在傳入的 DataFrame 尾部，或改由上層 `engine.py` 維護此狀態機，以確保狀態可跨 Tick 追蹤。
"""

# Append to implementation plan
content = content.replace("## Verification Plan", patch + "\n## Verification Plan")

with open("/home/shudgai999/.gemini/antigravity-ide/brain/ef18b717-86f8-4c84-9167-4e580134ef3e/implementation_plan.md", "w") as f:
    f.write(content)
print("Updated implementation_plan.md for State Machine")
