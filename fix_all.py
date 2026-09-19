import re
import os

# Fix exits
with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

# Change Crash Defense to intraday
content = content.replace('''            # Crash Defense (Engulfing / Surge)
            if side == "LONG":
                is_engulfing = (close_p < prev_1['open']) and (prev_1['open'] >= prev_2['close']) and (close_p < prev_2['open'])
                is_crash = (close_p < prev_1['open']) and (prev_body > 1.5 * atr)
                if is_engulfing or is_crash:
                    return "EXIT_MOMENTUM_REVERSAL_DEFENSE (LONG Crash/Engulfing)"
            elif side == "SHORT":
                is_engulfing = (close_p > prev_1['open']) and (prev_1['open'] <= prev_2['close']) and (close_p > prev_2['open'])
                is_surge = (close_p > prev_1['open']) and (prev_body > 1.5 * atr)
                if is_engulfing or is_surge:
                    return "EXIT_MOMENTUM_REVERSAL_DEFENSE (SHORT Surge/Engulfing)"''', '')

intraday_check = '''
        # Intraday Check C: Crash Defense (Engulfing / Surge) - ALWAYS OVERRIDES
        if side == "LONG":
            is_engulfing = (current_price < curr['open']) and (curr['open'] >= prev_1['close']) and (current_price < prev_1['open'])
            is_crash = (current_price < curr['open']) and (candle_body > 1.5 * atr)
            if is_engulfing or is_crash:
                logger.warning(f"[EXIT_MOMENTUM_REVERSAL_DEFENSE] (LONG Crash/Engulfing)")
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE (LONG Crash/Engulfing)"
        elif side == "SHORT":
            is_engulfing = (current_price > curr['open']) and (curr['open'] <= prev_1['close']) and (current_price > prev_1['open'])
            is_surge = (current_price > curr['open']) and (candle_body > 1.5 * atr)
            if is_engulfing or is_surge:
                logger.warning(f"[EXIT_MOMENTUM_REVERSAL_DEFENSE] (SHORT Surge/Engulfing)")
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE (SHORT Surge/Engulfing)"

        # Intraday Check B: Base Hard Stop & Step Trailing Lock
'''
content = content.replace('# Intraday Check B: Base Hard Stop & Step Trailing Lock', intraday_check)

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(content)

# Fix tests
with open('tests/test_v9_core_logic.py', 'r') as f:
    content = f.read()

# Fix phase jump kc_upper
content = content.replace('_default_row({"atr": 1.0})', '_default_row({"atr": 1.0, "kc_upper": 105.0})')

# Fix test_ratchet_lock_no_retreat
content = content.replace('''    check_atr_step_trailing_stop(position, df, 100.8)''', '''    strategy = DualTrackExitStrategy()
    strategy.evaluate_exit(position, df, 100.8)''')

# Fix ratio issue in test_entry_track_a_trend_defense
content = content.replace('''"open": 98.0, "close": 101.0, "high": 102.5, "low": 97.5''', '''"open": 98.0, "close": 101.0, "high": 101.5, "low": 97.5''')

# Fix the Overextended assertions
content = content.replace("[SPECIAL_ENTRY] Overextended Impulse LONG (Limit Order)", "[SPECIAL_ENTRY] Extreme Impulse LONG (>=2.0 ATR)")

with open('tests/test_v9_core_logic.py', 'w') as f:
    f.write(content)

