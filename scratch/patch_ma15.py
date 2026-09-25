with open("core/services/exits/profit_protection_service.py", "r") as f:
    content = f.read()

old_block = """    state['pending'] = bool(state.get('pending')) or triggered
    
    if not state.get('pending'):
        return None"""

new_block = """    # --- 5. MA3 穿越 MA15 停損 (防禦性出場) ---
    if not triggered and frame is not None and len(frame) >= 1:
        if 'ma3' in frame.columns and 'ma15' in frame.columns:
            curr_ma3_val = float(frame['ma3'].iloc[-1])
            curr_ma15_val = float(frame['ma15'].iloc[-1])
            if side == 'LONG' and curr_ma3_val < curr_ma15_val:
                triggered = True
                exit_reason = f'EXIT_MA3_CROSS_MA15: 多單防禦性出場！MA3 ({curr_ma3_val:.5f}) 跌破 MA15 ({curr_ma15_val:.5f})'
            elif side == 'SHORT' and curr_ma3_val > curr_ma15_val:
                triggered = True
                exit_reason = f'EXIT_MA3_CROSS_MA15: 空單防禦性出場！MA3 ({curr_ma3_val:.5f}) 突破 MA15 ({curr_ma15_val:.5f})'

    state['pending'] = bool(state.get('pending')) or triggered
    
    if not state.get('pending'):
        return None"""

content = content.replace(old_block, new_block)

with open("core/services/exits/profit_protection_service.py", "w") as f:
    f.write(content)
