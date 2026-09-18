import re

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

peak_exit_code = """
        # V5.1 峰值平倉 (Peak Momentum Exit)
        if side == "LONG":
            row = frame.iloc[-1]
            kc_upper = float(row.get('kc_upper', 0))
            if price >= kc_upper:
                # 滯漲判斷: 長上影線 或 實體縮減
                low = float(row.get('low', price))
                high = float(row.get('high', price))
                close = float(row.get('close', price))
                open_price = float(row.get('open', price))
                body = abs(close - open_price)
                upper_wick = high - max(open_price, close)
                
                if (body > 0 and upper_wick / body > 1.2) or (body < atr * 0.2):
                    # 在真實下單邏輯中，這會通知外部平倉 50%
                    return "EXIT_PEAK_MOMENTUM_PARTIAL"
"""

# Insert it before the RATCHET EXIT return
content = content.replace("            # 執行平倉", peak_exit_code + "\n            # 執行平倉")

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(content)
print("Updated dual_track_exit_service.py for Peak Momentum Exit")
