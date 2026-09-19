with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

old = '    if dist_from_middle > 2.0 * current_atr:\n        return False, "FILTERED_EXTREME_DISTANCE: Price too far from KC Middle (>2.0 ATR)", {}'

new = '''    if dist_from_middle > 2.0 * current_atr:
        # ── 斜率特權豁免（Trend Privilege）────────────────────────────
        # 以 ATR 無量綱化斜率，門檻 0.5 代表「每根 K 棒中軌移動 0.5 倍 ATR」
        STRONG_SLOPE_THRESHOLD = 0.5
        norm_slope_ma15   = slope_ma15   / current_atr
        norm_slope_middle = slope_middle / current_atr

        privilege_long  = (norm_slope_ma15 > STRONG_SLOPE_THRESHOLD or norm_slope_middle > STRONG_SLOPE_THRESHOLD)
        privilege_short = (norm_slope_ma15 < -STRONG_SLOPE_THRESHOLD or norm_slope_middle < -STRONG_SLOPE_THRESHOLD)

        if side == "LONG" and is_bullish and privilege_long:
            return True, "[TREND_PRIVILEGE_ENTRY] Extreme Distance Waived (Strong Bull Slope) LONG", {"action": "ENTER", "is_privileged": True}

        if side == "SHORT" and is_bearish and privilege_short:
            return True, "[TREND_PRIVILEGE_ENTRY] Extreme Distance Waived (Strong Bear Slope) SHORT", {"action": "ENTER", "is_privileged": True}

        # 弱勢/震盪：依然攔截
        return False, "FILTERED_EXTREME_DISTANCE: Price too far from KC Middle (>2.0 ATR)", {}'''

if old in content:
    content = content.replace(old, new)
    with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
        f.write(content)
    print("DONE")
else:
    # Try to find what's different
    import repr as r
    idx = content.find("FILTERED_EXTREME_DISTANCE")
    print(f"Found at idx={idx}")
    print(repr(content[idx-60:idx+120]))
