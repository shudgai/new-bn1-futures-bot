import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

new_functions = """def check_structural_alignment(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    ma3_prev1 = float(prev_1.get('ma3', prev_1.get('ema_3', 0)))
    ma3_prev2 = float(prev_2.get('ma3', prev_2.get('ema_3', 0)))
    
    slope_ma15 = ma15_prev1 - ma15_prev2
    slope_ma3 = ma3_prev1 - ma3_prev2
    
    if side == "LONG":
        ma15_ok = slope_ma15 >= 0
        ma3_ok = slope_ma3 > 1e-9
        if not ma15_ok: return False, "FILTERED_DUAL_RESONANCE: MA15 is falling"
        if not ma3_ok: return False, "FILTERED_DUAL_RESONANCE: MA3 not rising"
        return True, "OK"
        
    elif side == "SHORT":
        ma15_ok = slope_ma15 <= 0
        ma3_ok = slope_ma3 < -1e-9
        if not ma15_ok: return False, "FILTERED_DUAL_RESONANCE: MA15 is rising"
        if not ma3_ok: return False, "FILTERED_DUAL_RESONANCE: MA3 not falling"
        return True, "OK"
        
    return False, "INVALID_SIDE"

def check_extreme_pin_defense(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
    prev1_range = float(prev_1['high']) - float(prev_1['low'])
    prev1_body = abs(float(prev_1['close']) - float(prev_1['open']))
    
    prev2_range = float(prev_2['high']) - float(prev_2['low'])
    prev2_body = abs(float(prev_2['close']) - float(prev_2['open']))
    
    if prev1_range >= 1.5 * current_atr or prev1_body >= 1.5 * current_atr:
        return False, "FILTERED_EXTREME_PIN: Prev1 is extreme (>=1.5 ATR), waiting for next bar confirmation"
        
    if prev2_range >= 1.5 * current_atr or prev2_body >= 1.5 * current_atr:
        if side == "LONG":
            if float(prev_1['close']) <= float(prev_2['close']):
                return False, "FILTERED_EXTREME_PIN: Next bar failed to close above extreme candle's close"
        elif side == "SHORT":
            if float(prev_1['close']) >= float(prev_2['close']):
                return False, "FILTERED_EXTREME_PIN: Next bar failed to close below extreme candle's close"
                
    return True, "OK"
"""

pattern = re.compile(r'def check_structural_alignment.*?return False, "INVALID_SIDE"\n', re.DOTALL)
content = pattern.sub(new_functions, content)

insert_pattern = r'(    is_aligned, reject_reason = check_structural_alignment\(side, prev_1, prev_2, current_atr\)\n    if not is_aligned:\n        return False, reject_reason, \{\}\n)'
new_pin_check = r"""\1
    pin_passed, pin_reject_reason = check_extreme_pin_defense(side, prev_1, prev_2, current_atr)
    if not pin_passed:
        return False, pin_reject_reason, {}
"""
content = re.sub(insert_pattern, new_pin_check, content)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
print("Updated to Dual Resonance v3 and Pin Defense")
