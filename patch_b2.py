import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

replacement = """    # =========================================================================
    # 軌道 B-2：強化結構破軌 (Structural Breakout)
    # =========================================================================
    prev1_range = prev_high - prev_low
    prev1_body = body_length
    is_prev1_extreme = (prev1_range >= 1.5 * current_atr or prev1_body >= 1.5 * current_atr)
    
    prev2_range = float(prev_2['high']) - float(prev_2['low'])
    prev2_body = abs(float(prev_2['close']) - float(prev_2['open']))
    is_prev2_extreme = (prev2_range >= 1.5 * current_atr or prev2_body >= 1.5 * current_atr)
    
    is_struct_long = (prev_close > kc_mid_prev1) and (prev_close > ma15_prev1)
    is_struct_short = (prev_close < kc_mid_prev1) and (prev_close < ma15_prev1)
    
    is_mom_long = is_bullish and (body_length >= 0.8 * current_atr) and (body_ratio >= 0.5)
    is_mom_short = is_bearish and (body_length >= 0.8 * current_atr) and (body_ratio >= 0.5)

    if side == "LONG":
        if is_struct_long and is_mom_long and not is_prev1_extreme:
            return True, "[STRUCTURAL_BREAKOUT] Momentum Breakout LONG", {"action": "ENTER"}
            
        is_struct_long2 = (float(prev_2['close']) > kc_mid_prev2) and (float(prev_2['close']) > ma15_prev2)
        is_mom_long2 = (float(prev_2['close']) > float(prev_2['open'])) and (prev2_body >= 0.8 * current_atr) and ((prev2_body / prev2_range) >= 0.5 if prev2_range > 0 else False)
        
        if is_struct_long2 and is_mom_long2 and is_prev2_extreme:
            if prev_close > float(prev_2['close']):
                return True, "[DELAYED_BREAKOUT] Momentum LONG", {"action": "ENTER"}
                
    elif side == "SHORT":
        if is_struct_short and is_mom_short and not is_prev1_extreme:
            return True, "[STRUCTURAL_BREAKOUT] Momentum Breakout SHORT", {"action": "ENTER"}
            
        is_struct_short2 = (float(prev_2['close']) < kc_mid_prev2) and (float(prev_2['close']) < ma15_prev2)
        is_mom_short2 = (float(prev_2['close']) < float(prev_2['open'])) and (prev2_body >= 0.8 * current_atr) and ((prev2_body / prev2_range) >= 0.5 if prev2_range > 0 else False)
        
        if is_struct_short2 and is_mom_short2 and is_prev2_extreme:
            if prev_close < float(prev_2['close']):
                return True, "[DELAYED_BREAKOUT] Momentum SHORT", {"action": "ENTER"}

    # 1. 劇烈反噬冷卻檢測 (Post-Crash Cooldown)"""

pattern = re.compile(r'    # =========================================================================\n    # 軌道 B-2：強化結構破軌 \(Structural Breakout\)\n    # =========================================================================\n    # 1\. 劇烈反噬冷卻檢測 \(Post-Crash Cooldown\)')

content = pattern.sub(replacement, content)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
print("Patched Track B-2")
