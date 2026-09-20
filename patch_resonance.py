import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

new_func = """def check_structural_alignment(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    ma3_prev1 = float(prev_1.get('ma3', prev_1.get('ema_3', 0)))
    ma3_prev2 = float(prev_2.get('ma3', prev_2.get('ema_3', 0)))
    
    slope_ma15 = ma15_prev1 - ma15_prev2
    slope_ma3 = ma3_prev1 - ma3_prev2
    
    if side == "LONG":
        ma15_up = slope_ma15 > 1e-9  # 只要 > 0 即可，避免浮點數誤差
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

# Replace the old function
pattern = re.compile(r'def check_structural_alignment.*?return False, "INVALID_SIDE"\n', re.DOTALL)
content = pattern.sub(new_func, content)

# Remove the Gatekeeper from below Track P
gatekeeper_pattern = r'    # =========================================================================\n    # 趨勢結構審查 \(Trend Structural Gatekeeper\)\n    # =========================================================================\n    # 進入常規順勢進場 \(Track B, R, C\) 前，必須受嚴格的 MA15 結構與均線引力過濾\n    is_aligned, reject_reason = check_structural_alignment\(side, prev_1, prev_2, current_atr\)\n    if not is_aligned:\n        return False, reject_reason, \{\}\n'
content = re.sub(gatekeeper_pattern, '', content)

# Insert the new Dual Resonance Gatekeeper at the VERY TOP of check_streamlined_entry_signal
# We'll put it right after the first few variables are defined, before Track V.
insert_pattern = r'(    slope_ma15 = ma15_prev1 - ma15_prev2\n)'
new_gatekeeper = r"""\1
    # =========================================================================
    # 全局雙重共振守門員 (Global Dual Resonance Gatekeeper)
    # =========================================================================
    # 取消任何特例豁免，所有的開倉動作必須同時滿足 M3 與 MA15 同向
    is_aligned, reject_reason = check_structural_alignment(side, prev_1, prev_2, current_atr)
    if not is_aligned:
        return False, reject_reason, {}
"""
content = re.sub(insert_pattern, new_gatekeeper, content)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
print("Updated to Dual Resonance Gatekeeper")
