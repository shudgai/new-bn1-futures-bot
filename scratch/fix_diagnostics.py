import re

# 1. Modify unified_entry_strategy.py
with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    entry_content = f.read()

# Add a bypass for MA3/Sideways if strict breakout
ma3_block = """    if current_atr_val > 0 and not is_exempt_from_sideways:"""
ma3_bypass = """    # 【診斷修復】如果已經滿足嚴格的兩根破軌，直接豁免均線走平/糾纏過濾，避免錯失起跌/起漲點！
    is_strict_breakout_short = (prev_close < prev_kc_lower and close < kc_lower)
    is_strict_breakout_long = (prev_close > prev_kc_upper and close > kc_upper)
    is_exempt_from_sideways = is_exempt_from_sideways or is_strict_breakout_short or is_strict_breakout_long

    if current_atr_val > 0 and not is_exempt_from_sideways:"""
entry_content = entry_content.replace(ma3_block, ma3_bypass)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(entry_content)


# 2. Modify dual_track_exit_service.py
with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    exit_content = f.read()

exit_logic = """        # --- 滿足「真峰谷確認」後，才允許觸發平倉 ---
        if side == "LONG":
            if current_tp_price and eval_price <= current_tp_price:
                logger.warning(f"[EXIT_TRAILING_STOP] LONG {position.get('symbol', 'UNKNOWN')}: 跌破 1.5 ATR 本地防守線，波段獲利了結！")
                return "EXIT_TRAILING_STOP"
            if kc_trend == "DOWN":
                logger.warning(f"[EXIT_KC_REVERSAL] LONG {position.get('symbol', 'UNKNOWN')}: 漲勢竭盡且 KC 正式向下轉向，波段市價平倉了結！")
                return "EXIT_KC_REVERSAL"

        elif side == "SHORT":
            if current_tp_price and eval_price >= current_tp_price:
                logger.warning(f"[EXIT_TRAILING_STOP] SHORT {position.get('symbol', 'UNKNOWN')}: 突破 1.5 ATR 本地防守線，波段獲利了結！")
                return "EXIT_TRAILING_STOP"
            if kc_trend == "UP":
                logger.warning(f"[EXIT_KC_REVERSAL] SHORT {position.get('symbol', 'UNKNOWN')}: 跌勢竭盡且 KC 正式向上轉向，波段市價平倉了結！")
                return "EXIT_KC_REVERSAL\""""

new_exit_logic = """        # --- 滿足「真峰谷確認」後，才允許觸發平倉 ---
        ema_20 = float(current_kline.get('ema_20', current_kline.get('kc_middle', eval_price)))

        if side == "LONG":
            if current_tp_price and eval_price <= current_tp_price:
                # 【診斷修復】下跌中繼不輕易下車：即使跌破 1.5 ATR，也必須有效跌破 EMA20 中軌，否則視為洗盤
                if eval_price < ema_20:
                    logger.warning(f"[EXIT_TRAILING_STOP] LONG {position.get('symbol', 'UNKNOWN')}: 跌破 1.5 ATR 且跌破 EMA20，波段獲利了結！")
                    return "EXIT_TRAILING_STOP"
            if kc_trend == "DOWN":
                logger.warning(f"[EXIT_KC_REVERSAL] LONG {position.get('symbol', 'UNKNOWN')}: 漲勢竭盡且 KC 正式向下轉向，波段市價平倉了結！")
                return "EXIT_KC_REVERSAL"

        elif side == "SHORT":
            if current_tp_price and eval_price >= current_tp_price:
                # 【診斷修復】上漲中繼不輕易下車：即使突破 1.5 ATR，也必須有效突破 EMA20 中軌，否則視為洗盤
                if eval_price > ema_20:
                    logger.warning(f"[EXIT_TRAILING_STOP] SHORT {position.get('symbol', 'UNKNOWN')}: 突破 1.5 ATR 且突破 EMA20，波段獲利了結！")
                    return "EXIT_TRAILING_STOP"
            if kc_trend == "UP":
                logger.warning(f"[EXIT_KC_REVERSAL] SHORT {position.get('symbol', 'UNKNOWN')}: 跌勢竭盡且 KC 正式向上轉向，波段市價平倉了結！")
                return "EXIT_KC_REVERSAL\""""

exit_content = exit_content.replace(exit_logic, new_exit_logic)

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(exit_content)

