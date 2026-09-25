import re

with open("core/services/exits/profit_protection_service.py", "r") as f:
    content = f.read()

old_block_pattern = r"    # --- 3. 動態 ATR 追蹤鎖利.*?exit_reason = f'EXIT_PROFIT_LOCK_ATR: 空單從最低價 \{state\[\"peak_price\"\]:\.5f\} 彈升 0\.5 ATR，觸發鎖利出場 \(\{net:\.2f\}U\)'"

new_block = """    # --- 3. 真正的階梯鎖利 (4U鎖2U、6U鎖4U、每2U一階) ---
    locked_net_val = 0.0
    if peak_net >= 4.0:
        import math
        # 4->2, 6->4, 8->6, 10->8 ...
        locked_net_val = math.floor(peak_net / 2.0) * 2.0 - 2.0
        
        if not triggered and net <= locked_net_val:
            triggered = True
            exit_reason = f'EXIT_PROFIT_LOCK_STEP: 淨利從高點 {peak_net:.2f}U 回落，觸發真實階梯鎖利出場 (保底 {locked_net_val:.2f}U)'"""

content = re.sub(old_block_pattern, new_block, content, flags=re.DOTALL)

with open("core/services/exits/profit_protection_service.py", "w") as f:
    f.write(content)
