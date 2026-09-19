# Fix SHORT pullback_touched_bear: need prev_2 to have TESTED resistance but FAILED to close above it
with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

old = '''        kc_mid_prev2_val = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0)))
        pullback_touched_bear = (
            (kc_mid_prev2_val > 0 and prev2_high >= kc_mid_prev2_val * 0.99) or   # 反彈高點觸及 KC 中軌（含 1% 容差）
            (ma3_prev2 > 0 and prev2_high >= ma3_prev2 * 0.99)                     # 或觸及 MA3 阻力
        )'''

new = '''        kc_mid_prev2_val = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0)))
        # 回調反彈必須「測試阻力後失敗」：高點觸及 KC 中軌 or MA3，但收盤收在阻力之下
        pullback_tested_resistance = (
            (kc_mid_prev2_val > 0 and prev2_high >= kc_mid_prev2_val * 0.99) or   # 高點觸及 KC 中軌
            (ma3_prev2 > 0 and prev2_high >= ma3_prev2 * 0.99)                     # 或觸及 MA3 阻力
        )
        pullback_failed_resistance = (
            (kc_mid_prev2_val <= 0 or prev2_close_val < kc_mid_prev2_val) and  # 收盤未突破 KC 中軌
            (ma3_prev2 <= 0 or prev2_close_val < ma3_prev2)                     # 且收盤未突破 MA3
        )
        pullback_touched_bear = pullback_tested_resistance and pullback_failed_resistance'''

if old in content:
    content = content.replace(old, new)
    with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
        f.write(content)
    print("DONE")
else:
    print("NOT FOUND")
