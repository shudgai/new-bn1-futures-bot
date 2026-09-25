import re

with open("core/services/exits/profit_protection_service.py", "r") as f:
    content = f.read()

old_long_ma3 = """            # 真峰谷確認：MA3 回落 0.10 ATR，且現價跌破近3根已收盤K的最低點 (結構破位)
            structural_low = frame['low'].iloc[-4:-1].min() if len(frame) >= 4 else (entry - atr)
            if state['ma3_peak'] > 0 and (state['ma3_peak'] - curr_ma3) >= 0.10 * atr and (prev_ma3 - curr_ma3) >= 0.10 * atr:
                if price < structural_low:
                    triggered = True
                    exit_reason = f'EXIT_TRUE_MA3_PEAK: 多單真峰谷確認！MA3 衰退 0.10 ATR 且跌破結構前低 ({structural_low:.5f})'"""

new_long_ma3 = """            # 真峰谷確認：從峰頂回落至少 0.10 ATR，且當前 MA3 確定向下反轉 (curr_ma3 < prev_ma3)
            if state['ma3_peak'] > 0 and (state['ma3_peak'] - curr_ma3) >= 0.10 * atr and curr_ma3 < prev_ma3:
                triggered = True
                exit_reason = f'EXIT_TRUE_MA3_PEAK: 多單真峰谷確認！MA3 從最高點 {state["ma3_peak"]:.5f} 回落 0.10 ATR'"""

old_short_ma3 = """            # 真谷底確認：MA3 回升 0.10 ATR，且現價突破近3根已收盤K的最高點 (結構破位)
            structural_high = frame['high'].iloc[-4:-1].max() if len(frame) >= 4 else (entry + atr)
            if state['ma3_valley'] > 0 and (curr_ma3 - state['ma3_valley']) >= 0.10 * atr and (curr_ma3 - prev_ma3) >= 0.10 * atr:
                if price > structural_high:
                    triggered = True
                    exit_reason = f'EXIT_TRUE_MA3_VALLEY: 空單真谷底確認！MA3 衰退 0.10 ATR 且突破結構前高 ({structural_high:.5f})'"""

new_short_ma3 = """            # 真谷底確認：從谷底回升至少 0.10 ATR，且當前 MA3 確定向上反轉 (curr_ma3 > prev_ma3)
            if state['ma3_valley'] > 0 and (curr_ma3 - state['ma3_valley']) >= 0.10 * atr and curr_ma3 > prev_ma3:
                triggered = True
                exit_reason = f'EXIT_TRUE_MA3_VALLEY: 空單真谷底確認！MA3 從最低點 {state["ma3_valley"]:.5f} 彈升 0.10 ATR'"""

content = content.replace(old_long_ma3, new_long_ma3)
content = content.replace(old_short_ma3, new_short_ma3)

with open("core/services/exits/profit_protection_service.py", "w") as f:
    f.write(content)
