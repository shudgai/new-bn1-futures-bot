with open("core/services/exits/profit_protection_service.py", "r") as f:
    content = f.read()

old_block = """    # --- 5. MA3 穿越 MA15 停損 (防禦性出場) ---
    if not triggered and frame is not None and len(frame) >= 1:
        if 'ma3' in frame.columns and 'ma15' in frame.columns:
            curr_ma3_val = float(frame['ma3'].iloc[-1])
            curr_ma15_val = float(frame['ma15'].iloc[-1])
            if side == 'LONG' and curr_ma3_val < curr_ma15_val:
                triggered = True
                exit_reason = f'EXIT_MA3_CROSS_MA15: 多單防禦性出場！MA3 ({curr_ma3_val:.5f}) 跌破 MA15 ({curr_ma15_val:.5f})'
            elif side == 'SHORT' and curr_ma3_val > curr_ma15_val:
                triggered = True
                exit_reason = f'EXIT_MA3_CROSS_MA15: 空單防禦性出場！MA3 ({curr_ma3_val:.5f}) 突破 MA15 ({curr_ma15_val:.5f})'"""

new_block = """    # --- 5. 優化版：MA3 穿越 MA15 停損 (防禦性出場) ---
    # 增加「假回踩過濾器」：必須跌破 MA15 且 價格也跌破 MA15，並加上微小緩衝區
    if not triggered and frame is not None and len(frame) >= 1:
        if 'ma3' in frame.columns and 'ma15' in frame.columns:
            curr_ma3_val = float(frame['ma3'].iloc[-1])
            curr_ma15_val = float(frame['ma15'].iloc[-1])
            
            # 容錯緩衝 (0.02 ATR)，避免兩條線黏在一起時的微小抖動
            buffer = 0.02 * atr if atr > 0 else 0.0
            
            if side == 'LONG':
                # 條件：MA3 實質跌破 MA15，且最新價格也無力守住 MA15
                if curr_ma3_val < (curr_ma15_val - buffer) and price < curr_ma15_val:
                    triggered = True
                    exit_reason = f'EXIT_MA3_CROSS_MA15: 多單實質死叉！MA3 跌破 MA15 且價格失守'
            elif side == 'SHORT':
                if curr_ma3_val > (curr_ma15_val + buffer) and price > curr_ma15_val:
                    triggered = True
                    exit_reason = f'EXIT_MA3_CROSS_MA15: 空單實質金叉！MA3 突破 MA15 且價格失守'"""

content = content.replace(old_block, new_block)

with open("core/services/exits/profit_protection_service.py", "w") as f:
    f.write(content)
