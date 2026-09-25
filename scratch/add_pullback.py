import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

pullback_func = """
def check_pullback_continuation(df, side):
    \"\"\"
    結構化延續開倉邏輯 (Pullback Continuation)
    檢查是否為回踩受阻後重新跌破/突破外軌
    \"\"\"
    if len(df) < 10:
        return False, ""
    
    current = df.iloc[-2]
    close = float(current['close'])
    open_p = float(current['open'])
    kc_lower = float(current.get('kc_lower', close))
    kc_upper = float(current.get('kc_upper', close))
    ema_20 = float(current.get('ema_20', current.get('kc_middle', close)))
    atr = float(current.get('atr', 0))
    
    c_high = float(current['high'])
    c_low = float(current['low'])
    current_body = abs(close - open_p)
    c_range = c_high - c_low
    
    kc_m1 = float(df['kc_middle'].iloc[-2]) if 'kc_middle' in df.columns else ema_20
    kc_m2 = float(df['kc_middle'].iloc[-3]) if 'kc_middle' in df.columns else ema_20
    kc_m3 = float(df['kc_middle'].iloc[-4]) if 'kc_middle' in df.columns else ema_20
    
    kc_trend = 'FLAT'
    if kc_m1 > kc_m2 and kc_m2 > kc_m3:
        kc_trend = 'UP'
    elif kc_m1 < kc_m2 and kc_m2 < kc_m3:
        kc_trend = 'DOWN'

    if side == "SHORT":
        if kc_trend != 'DOWN':
            return False, ""
            
        # 當前 K 棒必須是陰線且收盤跌破下軌
        if close >= open_p or close >= kc_lower:
            return False, ""
            
        # 防追空護盾：乖離不可超過 2.2 * ATR
        if atr > 0 and (ema_20 - close) > 2.2 * atr:
            return False, ""
            
        # 防追空護盾：下影線不可超過實體 50%
        c_lower_shadow = min(close, open_p) - c_low
        if current_body > 0 and (c_lower_shadow / current_body) > 0.5:
            return False, ""
            
        # 回踩驗證：檢查過去 3-8 根 K 棒，是否曾經反彈觸碰或接近 KC 下軌/EMA20，且未實體站上 EMA20
        has_pullback = False
        for i in range(3, 9):
            if i > len(df):
                break
            past = df.iloc[-i]
            p_close = float(past['close'])
            p_high = float(past['high'])
            p_ema = float(past.get('ema_20', past.get('kc_middle', p_close)))
            p_lower = float(past.get('kc_lower', p_close))
            
            # 若反彈實體站上 EMA20，則趨勢已破壞，不視為弱勢中繼
            if p_close > p_ema:
                return False, ""
                
            # 判斷是否為有效反彈 (曾向上反彈觸碰或接近 KC 下軌)
            if p_high >= p_lower * 0.999: # 接近或突破下軌
                has_pullback = True
                
        if has_pullback:
            return True, "📉 [Pullback Cont] SHORT: 回踩受阻重啟，結構化延續開空！"
            
    elif side == "LONG":
        if kc_trend != 'UP':
            return False, ""
            
        # 當前 K 棒必須是陽線且收盤突破上軌
        if close <= open_p or close <= kc_upper:
            return False, ""
            
        # 防追高護盾：乖離不可超過 2.2 * ATR
        if atr > 0 and (close - ema_20) > 2.2 * atr:
            return False, ""
            
        # 防追高護盾：上影線不可超過實體 50%
        c_upper_shadow = c_high - max(close, open_p)
        if current_body > 0 and (c_upper_shadow / current_body) > 0.5:
            return False, ""
            
        has_pullback = False
        for i in range(3, 9):
            if i > len(df):
                break
            past = df.iloc[-i]
            p_close = float(past['close'])
            p_low = float(past['low'])
            p_ema = float(past.get('ema_20', past.get('kc_middle', p_close)))
            p_upper = float(past.get('kc_upper', p_close))
            
            # 若回踩實體跌破 EMA20，則趨勢已破壞
            if p_close < p_ema:
                return False, ""
                
            # 判斷是否為有效回踩 (曾向下觸碰或接近 KC 上軌)
            if p_low <= p_upper * 1.001:
                has_pullback = True
                
        if has_pullback:
            return True, "📈 [Pullback Cont] LONG: 回踩受阻重啟，結構化延續開多！"
            
    return False, ""

def check_streamlined_entry_signal"""

content = content.replace("def check_streamlined_entry_signal", pullback_func)

# Now, integrate it into evaluate_entry bypass

strict_block = """    # 【絕對硬防線 0 號】連續兩根實體破軌 (Strict 2-bar Breakout)
    if side == "LONG":
        if not (prev_close > prev_kc_upper and close > kc_upper):
            return False, "🛑 BLOCKED_STRICT_BREAKOUT (LONG requires 2 consecutive bars closing above KC Upper)", {}
    elif side == "SHORT":
        if not (prev_close < prev_kc_lower and close < kc_lower):
            return False, "🛑 BLOCKED_STRICT_BREAKOUT (SHORT requires 2 consecutive bars closing below KC Lower)", {}"""

strict_bypass = """    # 【絕對硬防線 0 號】連續兩根實體破軌 (Strict 2-bar Breakout)
    is_pullback, pullback_msg = check_pullback_continuation(df, side)
    
    if not is_pullback:
        if side == "LONG":
            if not (prev_close > prev_kc_upper and close > kc_upper):
                return False, "🛑 BLOCKED_STRICT_BREAKOUT (LONG requires 2 consecutive bars closing above KC Upper)", {}
        elif side == "SHORT":
            if not (prev_close < prev_kc_lower and close < kc_lower):
                return False, "🛑 BLOCKED_STRICT_BREAKOUT (SHORT requires 2 consecutive bars closing below KC Lower)", {}
    else:
        # 如果是回踩延續，直接放行並回傳
        return True, pullback_msg, {"action": "ENTER", "is_breakout": True, "entry_type": "PULLBACK_CONTINUATION"}"""

content = content.replace(strict_block, strict_bypass)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)

