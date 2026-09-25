import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

# Replace LONG shadow logic
old_long_shadow = """    if side == "LONG":
        if c_range > 0 and current_body > 0:
            if (c_upper_shadow / current_body > 0.5) or (c_upper_shadow / c_range > 0.35):
                return False, "🛑 BLOCKED_SHADOW_REJECTION (Upper shadow > 50% body or 35% range)", {}"""

new_long_shadow = """    live_close = float(df.iloc[-1]['close'])
    
    if side == "LONG":
        if c_range > 0 and current_body > 0:
            if (c_upper_shadow / current_body > 0.5) or (c_upper_shadow / c_range > 0.35):
                # 【優化】如果第三根（Live K）已經向上突破了這根帶上影線的高點，代表賣壓被化解，放行開倉！
                if live_close > c_high:
                    pass
                else:
                    return False, "🛑 BLOCKED_SHADOW_REJECTION (Upper shadow > 50% body or 35% range, and high not broken)", {}"""
content = content.replace(old_long_shadow, new_long_shadow)


# Replace SHORT shadow logic
old_short_shadow = """    elif side == "SHORT":
        if c_range > 0 and current_body > 0:
            if (c_lower_shadow / current_body > 0.5) or (c_lower_shadow / c_range > 0.35):
                return False, "🛑 BLOCKED_SHADOW_REJECTION (Lower shadow > 50% body or 35% range)", {}"""

new_short_shadow = """    elif side == "SHORT":
        if c_range > 0 and current_body > 0:
            if (c_lower_shadow / current_body > 0.5) or (c_lower_shadow / c_range > 0.35):
                # 【優化】如果第三根（Live K）已經向下突破了這根帶下影線的低點，代表買盤被化解，放行開倉！
                if live_close < c_low:
                    pass
                else:
                    return False, "🛑 BLOCKED_SHADOW_REJECTION (Lower shadow > 50% body or 35% range, and low not broken)", {}"""
content = content.replace(old_short_shadow, new_short_shadow)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)

