import re

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

# 1. Update Trailing Stop
old_trailing = """        # 啟動門檻：最高浮盈達到 0.7 ATR
        if max_profit_atr >= 0.7:
            locked_profit_atr = max_profit_atr - 0.7
            if unrealized_profit_atr <= locked_profit_atr:
                logger.warning(f"[EXIT_DYNAMIC_PROFIT_HARVEST] {side} profit dropped to {unrealized_profit_atr:.2f} ATR (locked: {locked_profit_atr:.2f} ATR) @ {curr_close:.6f}")
                return "EXIT_DYNAMIC_PROFIT_HARVEST\""""

new_trailing = """        # ══════════════════════════════════════════════════════════════
        # 優先級 2：固定鎖利 (硬性保底 3.0 ATR)
        # ══════════════════════════════════════════════════════════════
        FIXED_TP_ATR = 3.0
        if unrealized_profit_atr >= FIXED_TP_ATR:
            logger.warning(f"[EXIT_FIXED_TAKE_PROFIT] {side} hit fixed take profit ({FIXED_TP_ATR} ATR) @ {curr_close:.6f}")
            return "EXIT_FIXED_TAKE_PROFIT"

        # ══════════════════════════════════════════════════════════════
        # 優先級 2.5：動態獲利收割 (移動止盈 0.75 ATR 啟動 / 0.75 ATR 回撤)
        # ══════════════════════════════════════════════════════════════
        TRAILING_STOP_ATR = 0.75
        if max_profit_atr >= TRAILING_STOP_ATR:
            locked_profit_atr = max_profit_atr - TRAILING_STOP_ATR
            if unrealized_profit_atr <= locked_profit_atr:
                logger.warning(f"[EXIT_DYNAMIC_PROFIT_HARVEST] {side} profit dropped to {unrealized_profit_atr:.2f} ATR (locked: {locked_profit_atr:.2f} ATR) @ {curr_close:.6f}")
                return "EXIT_DYNAMIC_PROFIT_HARVEST\""""

content = content.replace(old_trailing, new_trailing)

# Wait, `unrealized_profit_atr` is calculated just above the trailing stop block in the original file:
#         if side == "LONG":
#             unrealized_profit_atr = (curr_close - entry_price) / atr
#         else:
#             unrealized_profit_atr = (entry_price - curr_close) / atr
#             
#         max_profit_atr = position.get("max_profit_atr", 0.0)

# The replace will just replace the IF statement. Let's make sure the replacement works.
with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(content)
print("Patched exit service")
