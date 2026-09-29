import re

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

new_content = re.sub(
    r"# =======================================================\n    # 【多單 \(LONG\) 出場標準】防範「賣壓」\n    # =======================================================\n    if sign == 1:.*?(?=    # =======================================================\n    # 【空單 \(SHORT\) 出場標準】防範「買盤反撲」)",
    r"""# =======================================================
    # 【多單 (LONG) 出場標準】高位賣壓
    # =======================================================
    if sign == 1:
        unrealized_pnl = float(position.get('unrealized_pnl', 0.0))
        entry_price = float(position.get('entry_price', 0.0))
        roi = sign * (close - entry_price) / entry_price if entry_price > 0 else 0

        # 一、 前提條件：必須「漲很高」（未達高位嚴禁觸發賣壓平倉）
        is_high_level = (unrealized_pnl >= 2.5 or roi >= 0.012) and \
                        ((close - float(c.kc_upper) >= 0.8 * atr) or (close - float(c.ma15) >= 1.5 * atr))

        if is_high_level:
            # 二、 觸發條件：高位出現「實質巨額賣壓」
            # 1. 【高位巨長上影墓碑線】
            upper_wick = c_high - max(c_open, close)
            body = abs(close - c_open)
            if upper_wick >= 1.5 * body and close < (c_high + c_low) / 2.0:
                return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_LONG_HIGH_PIN_BAR_PRESSURE')
            
            # 2. 【高位大陰線反包】
            if close < c_open and body >= 0.8 * atr:
                return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_LONG_HIGH_BEARISH_ENGULFING')
                
            # 3. 【高位 MA3 拐頭向下 + 連續 2 根陰線】
            if len(closed) >= 2:
                ma3_curr = float(c.ma3)
                ma3_prev = float(c1.ma3)
                two_bear = (float(c.close) < float(c.open)) and (float(c1.close) < float(c1.open))
                if ma3_curr < ma3_prev and two_bear:
                    return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_LONG_HIGH_MA3_HOOK_DOWN')

        # 2. 當根收盤【跌破 MA15】（生命線防守，無論高低位）
        ma15 = float(c.get('ma15', c.get('ma3', close))) if hasattr(c, 'get') else float(getattr(c, 'ma15', getattr(c, 'ma3', close)))
        if close < ma15:
            return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_LONG_BELOW_MA15_LIFELINE')

""",
    content,
    flags=re.DOTALL
)

new_content = re.sub(
    r"# =======================================================\n    # 【空單 \(SHORT\) 出場標準】防範「買盤反撲」\n    # =======================================================\n    elif sign == -1:.*?(?=    return dict\(should_exit=False\))",
    r"""# =======================================================
    # 【空單 (SHORT) 出場標準】防範「買盤反撲」
    # =======================================================
    elif sign == -1:
        unrealized_pnl = float(position.get('unrealized_pnl', 0.0))
        entry_price = float(position.get('entry_price', 0.0))
        roi = sign * (close - entry_price) / entry_price if entry_price > 0 else 0

        # 一、 前提條件：必須「跌很深」（未達低位嚴禁觸發買盤平倉）
        is_low_level = (unrealized_pnl >= 2.5 or roi >= 0.012) and \
                       ((float(c.kc_lower) - close >= 0.8 * atr) or (float(c.ma15) - close >= 1.5 * atr))

        if is_low_level:
            # 二、 觸發條件：低位出現「實質巨額買盤」
            # 1. 【低位巨長下影槌子線】
            lower_wick = min(c_open, close) - c_low
            body = abs(close - c_open)
            if lower_wick >= 1.5 * body and close > (c_high + c_low) / 2.0:
                return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_SHORT_LOW_PIN_BAR_PRESSURE')
            
            # 2. 【低位大陽線反包】
            if close > c_open and body >= 0.8 * atr:
                return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_SHORT_LOW_BULLISH_ENGULFING')
                
            # 3. 【低位 MA3 拐頭向上 + 連續 2 根陽線】
            if len(closed) >= 2:
                ma3_curr = float(c.ma3)
                ma3_prev = float(c1.ma3)
                two_bull = (float(c.close) > float(c.open)) and (float(c1.close) > float(c1.open))
                if ma3_curr > ma3_prev and two_bull:
                    return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_SHORT_LOW_MA3_HOOK_UP')

        # 2. 當根收盤【漲破 MA15】（生命線防守，無論高低位）
        ma15 = float(c.get('ma15', c.get('ma3', close))) if hasattr(c, 'get') else float(getattr(c, 'ma15', getattr(c, 'ma3', close)))
        if close > ma15:
            return dict(should_exit=True, action='FULL_CLOSE', reason='EXIT_SHORT_ABOVE_MA15_LIFELINE')

""",
    new_content,
    flags=re.DOTALL
)

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(new_content)
