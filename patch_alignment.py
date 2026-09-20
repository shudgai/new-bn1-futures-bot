import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

new_func = """def check_structural_alignment(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0)))
    kc_mid_prev2 = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0)))
    close_prev1 = float(prev_1['close'])
    
    slope_ma15 = ma15_prev1 - ma15_prev2
    slope_kc_mid = kc_mid_prev1 - kc_mid_prev2
    
    if side == "LONG":
        # 放寬斜率要求，只要大於 0.02 ATR 即視為有傾角
        ma15_ok = slope_ma15 > 0.02 * current_atr
        kc_mid_ok = slope_kc_mid > 0
        # 移除了 KC_Middle 和 MA15 的排列約束，因為 EMA20 和 SMA15 誰快誰慢在不同波動率下會交叉，容易誤攔
        alignment_ok = (close_prev1 > kc_mid_prev1) and (close_prev1 > ma15_prev1)
        # 放寬空間過濾，0.3 ATR
        distance_ok = (close_prev1 - ma15_prev1) > (current_atr * 0.3)
        
        if not ma15_ok: return False, "FILTERED_STRUCTURE: MA15 not trending up (needs >0.02 ATR slope)"
        if not kc_mid_ok: return False, "FILTERED_STRUCTURE: KC Middle not trending up"
        if not alignment_ok: return False, "FILTERED_STRUCTURE: Price not above both KC_Mid and MA15"
        if not distance_ok: return False, "FILTERED_STRUCTURE: Price too close to MA15 (<0.3 ATR)"
        return True, "OK"
        
    elif side == "SHORT":
        ma15_ok = slope_ma15 < -0.02 * current_atr
        kc_mid_ok = slope_kc_mid < 0
        alignment_ok = (close_prev1 < kc_mid_prev1) and (close_prev1 < ma15_prev1)
        distance_ok = (ma15_prev1 - close_prev1) > (current_atr * 0.3)
        
        if not ma15_ok: return False, "FILTERED_STRUCTURE: MA15 not trending down (needs <-0.02 ATR slope)"
        if not kc_mid_ok: return False, "FILTERED_STRUCTURE: KC Middle not trending down"
        if not alignment_ok: return False, "FILTERED_STRUCTURE: Price not below both KC_Mid and MA15"
        if not distance_ok: return False, "FILTERED_STRUCTURE: Price too close to MA15 (<0.3 ATR)"
        return True, "OK"
        
    return False, "INVALID_SIDE"
"""

# Replace the old function
pattern = re.compile(r'def check_structural_alignment.*?return False, "INVALID_SIDE"\n', re.DOTALL)
content = pattern.sub(new_func, content)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
print("Updated structural alignment")
