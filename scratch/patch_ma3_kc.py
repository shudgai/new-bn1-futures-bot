import re

with open("core/services/exits/profit_protection_service.py", "r") as f:
    content = f.read()

# For LONG: only update ma3_peak if curr_ma3 > kc_upper.
# But wait, if curr_ma3 is max, we must ensure it was outside KC when it hit the peak.
# We can just check if curr_ma3 > upper, then record it.
# Actually, the requirement is: "峰頂須嚴格在同根 CK 上軌外".
old_long_peak = """        if side == 'LONG':
            state['ma3_peak'] = max(state.get('ma3_peak', curr_ma3), curr_ma3)"""

new_long_peak = """        if side == 'LONG':
            kc_upper = float(frame['kc_upper'].iloc[-1]) if 'kc_upper' in frame.columns else 0.0
            # 只有當 MA3 嚴格在 CK 上軌外時，才允許刷新峰值 (符合「限 CK 外峰谷」規則)
            if curr_ma3 > kc_upper:
                state['ma3_peak'] = max(state.get('ma3_peak', curr_ma3), curr_ma3)"""

old_short_valley = """        elif side == 'SHORT':
            state['ma3_valley'] = min(state.get('ma3_valley', curr_ma3), curr_ma3)"""

new_short_valley = """        elif side == 'SHORT':
            kc_lower = float(frame['kc_lower'].iloc[-1]) if 'kc_lower' in frame.columns else float('inf')
            # 只有當 MA3 嚴格在 CK 下軌外時，才允許刷新谷底
            if curr_ma3 < kc_lower:
                state['ma3_valley'] = min(state.get('ma3_valley', curr_ma3), curr_ma3)"""

content = content.replace(old_long_peak, new_long_peak)
content = content.replace(old_short_valley, new_short_valley)

with open("core/services/exits/profit_protection_service.py", "w") as f:
    f.write(content)
