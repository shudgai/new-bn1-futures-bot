with open("core/services/exits/profit_protection_service.py", "r") as f:
    content = f.read()

old_block = """    # --- 3. 真正的階梯鎖利 (4U鎖2U、6U鎖4U、每2U一階) ---
    locked_net_val = 0.0
    if peak_net >= 4.0:
        import math
        # 4->2, 6->4, 8->6, 10->8 ...
        locked_net_val = math.floor(peak_net / 2.0) * 2.0 - 2.0
        state['locked_net'] = locked_net_val  # 確保前端能即時讀取到最新的鎖利金額"""

new_block = """    # --- 3. 真正的階梯鎖利 (4U鎖2U、6U鎖4U、每2U一階) ---
    locked_net_val = 0.0
    if peak_net >= 4.0:
        import math
        # 4->2, 6->4, 8->6, 10->8 ...
        locked_net_val = math.floor(peak_net / 2.0) * 2.0 - 2.0
        state['locked_net'] = locked_net_val  # 確保前端能即時讀取到最新的鎖利金額
    
    # 寫入即時日誌
    if peak_net > 1.0:
        with open("data/locked_net_debug.log", "a") as dbgf:
            dbgf.write(f"[{position.get('symbol', 'UNK')}] price={price}, net={net:.4f}, peak_net={peak_net:.4f}, locked_net_val={locked_net_val}\\n")"""

content = content.replace(old_block, new_block)

with open("core/services/exits/profit_protection_service.py", "w") as f:
    f.write(content)
