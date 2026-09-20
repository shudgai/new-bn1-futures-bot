import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

new_defense = """def check_extreme_pin_defense(side: str, prev_1: pd.Series, prev_2: pd.Series, current_atr: float) -> tuple[bool, str]:
    prev1_range = float(prev_1['high']) - float(prev_1['low'])
    prev1_body = abs(float(prev_1['close']) - float(prev_1['open']))
    
    prev2_range = float(prev_2['high']) - float(prev_2['low'])
    prev2_body = abs(float(prev_2['close']) - float(prev_2['open']))
    
    is_prev1_extreme = (prev1_range >= 1.5 * current_atr or prev1_body >= 1.5 * current_atr)
    is_prev2_extreme = (prev2_range >= 1.5 * current_atr or prev2_body >= 1.5 * current_atr)
    
    if is_prev2_extreme:
        if side == "LONG":
            if float(prev_1['close']) <= float(prev_2['close']):
                return False, "FILTERED_EXTREME_PIN: Next bar failed to close above extreme candle's close"
            else:
                return True, "OK"
        elif side == "SHORT":
            if float(prev_1['close']) >= float(prev_2['close']):
                return False, "FILTERED_EXTREME_PIN: Next bar failed to close below extreme candle's close"
            else:
                return True, "OK"
                
    if is_prev1_extreme:
        return False, "FILTERED_EXTREME_PIN: Prev1 is extreme (>=1.5 ATR), waiting for next bar confirmation"
        
    return True, "OK"
"""

pattern = re.compile(r'def check_extreme_pin_defense.*?return True, "OK"\n', re.DOTALL)
content = pattern.sub(new_defense, content)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
print("Patched check_extreme_pin_defense")
