import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

# Define the new Track B-2 logic
new_track_b2 = """    # =========================================================================
    # 軌道 B-2：動能共振與結構突破 (Structural Momentum Breakout)
    # =========================================================================
    # 1. 檢查剛收盤 K 棒 (prev_1) 的標準突破
    is_normal_breakout = False
    if side == "LONG":
        is_struct_long = (prev_close > kc_mid_prev1) and (prev_close > ma15_prev1)
        is_mom_long = is_bullish and (body_length >= 0.8 * current_atr) and (body_ratio >= 0.5)
        if is_struct_long and is_mom_long:
            is_normal_breakout = True
    elif side == "SHORT":
        is_struct_short = (prev_close < kc_mid_prev1) and (prev_close < ma15_prev1)
        is_mom_short = is_bearish and (body_length >= 0.8 * current_atr) and (body_ratio >= 0.5)
        if is_struct_short and is_mom_short:
            is_normal_breakout = True

    # 2. 檢查上一根 K 棒 (prev_2) 的極端突破與次根確認 (延遲進場)
    is_delayed_breakout = False
    prev2_close = float(prev_2['close'])
    prev2_open = float(prev_2['open'])
    prev2_body = abs(prev2_close - prev2_open)
    prev2_range = float(prev_2['high']) - float(prev_2['low'])
    prev2_body_ratio = prev2_body / prev2_range if prev2_range > 0 else 0
    kc_mid_prev2 = float(prev_2.get('kc_middle', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    is_bullish_prev2 = prev2_close > prev2_open
    is_bearish_prev2 = prev2_close < prev2_open

    if side == "LONG":
        is_struct_long2 = (prev2_close > kc_mid_prev2) and (prev2_close > ma15_prev2)
        is_mom_long2 = is_bullish_prev2 and (prev2_body >= 0.8 * current_atr) and (prev2_body_ratio >= 0.5)
        is_extreme2 = (prev2_body >= 1.5 * current_atr) or (prev2_range >= 1.5 * current_atr)
        
        # 如果 prev_2 是極端突破，檢查 prev_1 是否成功確認 (收盤價 > prev_2 收盤價)
        if is_struct_long2 and is_mom_long2 and is_extreme2:
            if prev_close > prev2_close:
                is_delayed_breakout = True
                
    elif side == "SHORT":
        is_struct_short2 = (prev2_close < kc_mid_prev2) and (prev2_close < ma15_prev2)
        is_mom_short2 = is_bearish_prev2 and (prev2_body >= 0.8 * current_atr) and (prev2_body_ratio >= 0.5)
        is_extreme2 = (prev2_body >= 1.5 * current_atr) or (prev2_range >= 1.5 * current_atr)
        
        # 如果 prev_2 是極端突破，檢查 prev_1 是否成功確認 (收盤價 < prev_2 收盤價)
        if is_struct_short2 and is_mom_short2 and is_extreme2:
            if prev_close < prev2_close:
                is_delayed_breakout = True

    # 3. 觸發進場
    if is_normal_breakout or is_delayed_breakout:
        action_msg = "[DELAYED_BREAKOUT]" if is_delayed_breakout else "[STRUCTURAL_BREAKOUT]"
        return True, f"{action_msg} Momentum {side}", {"action": "ENTER"}
"""

# Replace the existing Track B-2
# Regex matches from Track B-2 start to just before Track R
pattern = re.compile(
    r'    # =========================================================================\n'
    r'    # 軌道 B-2：動能共振與結構突破 \(Structural Momentum Breakout\)\n'
    r'    # =========================================================================.*?(?=    # =========================================================================\n    # 軌道 R：波段回踩 \(Trend Retracement\))',
    re.DOTALL
)

content = pattern.sub(new_track_b2, content)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
print("Updated Track B-2 successfully")
