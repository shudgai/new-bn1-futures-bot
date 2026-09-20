import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

# I will just rewrite the whole check_structural_alignment correctly and remove the duplicate call.
new_func = """def check_structural_alignment(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    ma3_prev1 = float(prev_1.get('ma3', prev_1.get('ema_3', 0)))
    ma3_prev2 = float(prev_2.get('ma3', prev_2.get('ema_3', 0)))
    
    slope_ma15 = ma15_prev1 - ma15_prev2
    slope_ma3 = ma3_prev1 - ma3_prev2
    
    if side == "LONG":
        ma15_up = slope_ma15 > 1e-9
        ma3_up = slope_ma3 > 1e-9
        
        if not ma15_up: return False, "FILTERED_DUAL_RESONANCE: MA15 not rising"
        if not ma3_up: return False, "FILTERED_DUAL_RESONANCE: MA3 not rising"
        return True, "OK"
        
    elif side == "SHORT":
        ma15_down = slope_ma15 < -1e-9
        ma3_down = slope_ma3 < -1e-9
        
        if not ma15_down: return False, "FILTERED_DUAL_RESONANCE: MA15 not falling"
        if not ma3_down: return False, "FILTERED_DUAL_RESONANCE: MA3 not falling"
        return True, "OK"
        
    return False, "INVALID_SIDE"
"""

pattern = re.compile(r'def check_structural_alignment.*?return False, "INVALID_SIDE"\n', re.DOTALL)
content = pattern.sub(new_func, content)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
print("Fixed recursion")
