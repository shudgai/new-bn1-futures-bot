with open('core/indicators.py', 'r') as f:
    content = f.read()

# 1. Update is_peak and is_trough
old_peaks = """    # 2. 判斷基本峰谷
    is_trough = (ma5_curr > ma5_prev) and (ma5_prev < ma5_prev2)
    is_peak = (ma5_curr < ma5_prev) and (ma5_prev > ma5_prev2)"""

new_peaks = """    # 2. 判斷基本峰谷 (提早偵測：改用更敏銳的 MA3 判定轉折，減少進場延遲)
    ma3_curr = float(df['ma3'].iloc[-1])
    ma3_prev = float(df['ma3'].iloc[-2])
    ma3_prev2 = float(df['ma3'].iloc[-3])
    
    is_trough = (ma3_curr > ma3_prev) and (ma3_prev < ma3_prev2)
    is_peak = (ma3_curr < ma3_prev) and (ma3_prev > ma3_prev2)"""

content = content.replace(old_peaks, new_peaks)

# 2. Update scoring
old_score = """    # 綜合「真反轉」確認分數
    bull_confirm_score = sum([is_bullish_pinbar, is_bullish_div, is_choch_up, is_green])
    bear_confirm_score = sum([is_bearish_pinbar, is_bearish_div, is_choch_down, is_red])"""

new_score = """    # 綜合「真反轉」確認分數 (剔除顏色雜訊，純看動能與結構破壞)
    bull_confirm_score = sum([is_bullish_pinbar, is_bullish_div, is_choch_up])
    bear_confirm_score = sum([is_bearish_pinbar, is_bearish_div, is_choch_down])"""

content = content.replace(old_score, new_score)

# 3. Update conditions in Priority 2
old_cond_1 = """    if ma5_curr < ma25_curr:
        if is_trough and bull_confirm_score >= 3:"""

new_cond_1 = """    if ma5_curr < ma25_curr:
        if is_trough and is_green and bull_confirm_score >= 1:"""

content = content.replace(old_cond_1, new_cond_1)

old_cond_2 = """    if ma5_curr > ma25_curr:
        if is_peak and bear_confirm_score >= 3:"""

new_cond_2 = """    if ma5_curr > ma25_curr:
        if is_peak and is_red and bear_confirm_score >= 1:"""

content = content.replace(old_cond_2, new_cond_2)

# Update reasons text
content = content.replace("({bull_confirm_score}項條件)", "(>=1項結構條件)")
content = content.replace("({bear_confirm_score}項條件)", "(>=1項結構條件)")


with open('core/indicators.py', 'w') as f:
    f.write(content)

