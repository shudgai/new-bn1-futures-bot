import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

# 1. Update signature
content = content.replace(
    "def check_streamlined_entry_signal(df, side: str, live_price: float) -> tuple[bool, str]:",
    "def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str]:"
)

# 2. Update Bandwidth
old_bw = """    # 1. 帶寬保護：過濾死水盤
    if (latest["kc_upper"] - latest["kc_lower"]) < (1.0 * atr):
        return False, "BLOCK_BANDWIDTH_TOO_FLAT\""""
new_bw = """    # 1. 帶寬保護：過濾死水盤 (V5.2 動態寬度過濾)
    is_bandwidth_ok = (latest["kc_upper"] - latest["kc_lower"]) >= (1.0 * atr)
    atr_expanding = False
    if len(df) > 6:
        recent_atr = df['atr'].iloc[-3:].mean()
        prev_atr = df['atr'].iloc[-6:-3].mean()
        if prev_atr > 0 and (recent_atr / prev_atr - 1) >= 0.20:
            atr_expanding = True
    if not is_bandwidth_ok and not atr_expanding:
        return False, "BLOCK_BANDWIDTH_TOO_FLAT\""""
content = content.replace(old_bw, new_bw)

# 3. Update Peak/Valley (Space Tolerance Zone)
old_pv = """    # 0. 峰谷過濾：防止追高殺跌 ( > 1.5 ATR 直接擋掉)
    dist_from_mid = abs(live_price - kc_mid)
    if dist_from_mid > (1.5 * atr):
        return False, "BLOCK_EXTREME_PEAK_OR_VALLEY\""""
new_pv = """    # 0. 峰谷過濾：防止追高殺跌 ( > 1.5 ATR 擋掉, 空間寬容帶 V5.2)
    dist_from_mid = abs(live_price - kc_mid)
    if dist_from_mid > (1.5 * atr):
        velocity_slowdown = kwargs.get("velocity_slowdown", False)
        # 容許延伸至 1.8 ATR (相當於 kc_upper + 0.3 ATR)
        if dist_from_mid <= (1.8 * atr) and velocity_slowdown:
            pass
        else:
            return False, "BLOCK_EXTREME_PEAK_OR_VALLEY\""""
content = content.replace(old_pv, new_pv)

# 4. Pass kwargs down
content = content.replace(
    "ok, reason = check_streamlined_entry_signal(frame, side, price)",
    "ok, reason = check_streamlined_entry_signal(frame, side, price, **kwargs)"
)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
