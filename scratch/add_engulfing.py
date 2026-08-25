with open('core/indicators.py', 'r') as f:
    content = f.read()

import re

# Add engulfing logic
old_logic = """    is_bullish_pinbar = is_green and (lower_shadow > body * 1.5) and (upper_shadow < body)
    is_bearish_pinbar = is_red and (upper_shadow > body * 1.5) and (lower_shadow < body)"""

new_logic = """    is_bullish_pinbar = is_green and (lower_shadow > body * 1.5) and (upper_shadow < body)
    is_bearish_pinbar = is_red and (upper_shadow > body * 1.5) and (lower_shadow < body)
    
    # 新增吞噬型態 (Engulfing)
    prev_open = float(open_p.iloc[-2])
    prev_close = float(close.iloc[-2])
    is_prev_red = prev_close < prev_open
    is_prev_green = prev_close > prev_open
    is_bullish_engulfing = is_green and is_prev_red and (last_close > prev_open) and (last_open < prev_close)
    is_bearish_engulfing = is_red and is_prev_green and (last_close < prev_open) and (last_open > prev_close)"""

content = content.replace(old_logic, new_logic)

# Update score
old_score = """    # 綜合「真反轉」確認分數 (剔除顏色雜訊，純看動能與結構破壞)
    bull_confirm_score = sum([is_bullish_pinbar, is_bullish_div, is_choch_up])
    bear_confirm_score = sum([is_bearish_pinbar, is_bearish_div, is_choch_down])"""

new_score = """    # 綜合「真反轉」確認分數 (剔除顏色雜訊，純看動能與結構破壞)
    bull_confirm_score = sum([is_bullish_pinbar, is_bullish_div, is_choch_up, is_bullish_engulfing])
    bear_confirm_score = sum([is_bearish_pinbar, is_bearish_div, is_choch_down, is_bearish_engulfing])"""

content = content.replace(old_score, new_score)

with open('core/indicators.py', 'w') as f:
    f.write(content)

