import re

with open("core/services/exits/profit_protection_service.py", "r") as f:
    content = f.read()

old_block = """    # --- 3. 獲利回吐鎖利 (1U 啟動，20% 回吐) ---
    locked_net_val = 0.0
    if not triggered and peak_net >= 1.0:
        locked_net_val = peak_net * 0.8  # 保留 80% 利潤 (回吐 20%)
        if net <= locked_net_val:
            triggered = True
            exit_reason = f'EXIT_PROFIT_LOCK: 淨利從高點 {peak_net:.2f}U 回吐 20%，觸發鎖利出場 ({net:.2f}U)'"""

new_block = """    # --- 3. 動態 ATR 追蹤鎖利 (淨利 > 1U 啟動，最高點回落 0.5 ATR 平倉) ---
    locked_net_val = 0.0  # 僅為相容回傳值
    if side == 'LONG':
        state['peak_price'] = max(state.get('peak_price', price), price)
        if not triggered and peak_net >= 1.0:
            if state['peak_price'] - price >= 0.5 * atr:
                triggered = True
                exit_reason = f'EXIT_PROFIT_LOCK_ATR: 多單從最高價 {state["peak_price"]:.5f} 回落 0.5 ATR，觸發鎖利出場 ({net:.2f}U)'
    elif side == 'SHORT':
        # 空單找最低點
        state['peak_price'] = min(state.get('peak_price', price), price)
        if not triggered and peak_net >= 1.0:
            if price - state['peak_price'] >= 0.5 * atr:
                triggered = True
                exit_reason = f'EXIT_PROFIT_LOCK_ATR: 空單從最低價 {state["peak_price"]:.5f} 彈升 0.5 ATR，觸發鎖利出場 ({net:.2f}U)'"""

content = content.replace(old_block, new_block)

with open("core/services/exits/profit_protection_service.py", "w") as f:
    f.write(content)
