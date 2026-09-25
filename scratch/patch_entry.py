with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

old_block = """        c1 = frame.iloc[-2]
        c2 = frame.iloc[-1]
        atr = float(c2.get("atr", 0))"""

new_block = """        # c_prev: 上一根已收線
        # c_live: 當前未收線
        c_prev = frame.iloc[-2]
        c_live = frame.iloc[-1]
        atr = float(c_live.get("atr", 0))"""

content = content.replace(old_block, new_block)

old_block_2 = """        c2_body = abs(c2["close"] - c2["open"])

        # ==================== 1. 大動能長實體：第一根收盤即刻開倉 ====================
        # 多單：當前這根 c2 剛好爆破上軌，且是超大實體 (>= 1.2 ATR)
        if (
            side == "LONG"
            and c2["close"] > c2.get("kc_upper", 0)
            and c2["close"] > c2["open"]
            and c2_body >= 1.2 * atr
        ):
            return True, "MOMENTUM_BREAKOUT_C1_LONG", {
                "action": "ENTER",
                "side": "LONG",
                "reason": "MOMENTUM_BREAKOUT_C1_LONG",
                "entry_atr": atr,
            }

        # 空單：當前這根 c2 剛好爆破下軌，且是超大實體 (>= 1.2 ATR)
        if (
            side == "SHORT"
            and c2["close"] < c2.get("kc_lower", 0)
            and c2["close"] < c2["open"]
            and c2_body >= 1.2 * atr
        ):
            return True, "MOMENTUM_BREAKOUT_C1_SHORT", {
                "action": "ENTER",
                "side": "SHORT",
                "reason": "MOMENTUM_BREAKOUT_C1_SHORT",
                "entry_atr": atr,
            }

        # ==================== 2. 常規突破：等第二根 (c2) 收盤確認 ====================
        # 多單：c1 破上軌，c2 收盤依然留於上軌外
        if side == "LONG" and c1["close"] > c1.get("kc_upper", 0) and c2["close"] > c2.get("kc_upper", 0):
            return True, "CONFIRMED_KC_BREAKOUT_LONG", {
                "action": "ENTER",
                "side": "LONG",
                "reason": "CONFIRMED_KC_BREAKOUT_LONG",
                "entry_atr": atr,
            }

        # 空單：c1 破下軌，c2 收盤依然留於下軌外
        if side == "SHORT" and c1["close"] < c1.get("kc_lower", 0) and c2["close"] < c2.get("kc_lower", 0):
            return True, "CONFIRMED_KC_BREAKOUT_SHORT", {
                "action": "ENTER",
                "side": "SHORT",
                "reason": "CONFIRMED_KC_BREAKOUT_SHORT",
                "entry_atr": atr,
            }"""

new_block_2 = """        c_prev_body = abs(c_prev["close"] - c_prev["open"])

        # ==================== 1. 大動能長實體：第一根「收盤」即刻開倉 ====================
        # 也就是看剛收盤的那根 (c_prev) 是否滿足大實體破軌
        if (
            side == "LONG"
            and c_prev["close"] > c_prev.get("kc_upper", 0)
            and c_prev["close"] > c_prev["open"]
            and c_prev_body >= 1.2 * atr
        ):
            return True, "MOMENTUM_BREAKOUT_C1_LONG", {
                "action": "ENTER",
                "side": "LONG",
                "reason": "MOMENTUM_BREAKOUT_C1_LONG",
                "entry_atr": atr,
            }

        if (
            side == "SHORT"
            and c_prev["close"] < c_prev.get("kc_lower", 0)
            and c_prev["close"] < c_prev["open"]
            and c_prev_body >= 1.2 * atr
        ):
            return True, "MOMENTUM_BREAKOUT_C1_SHORT", {
                "action": "ENTER",
                "side": "SHORT",
                "reason": "MOMENTUM_BREAKOUT_C1_SHORT",
                "entry_atr": atr,
            }

        # ==================== 2. 常規突破：必須連續兩根 K 線的「收盤價」都在軌外 ====================
        # 看倒數第二根收線 (c_prev2) 和 倒數第一根收線 (c_prev)
        if len(frame) >= 4:
            c_prev2 = frame.iloc[-3]
            
            if side == "LONG" and c_prev2["close"] > c_prev2.get("kc_upper", 0) and c_prev["close"] > c_prev.get("kc_upper", 0):
                return True, "CONFIRMED_KC_BREAKOUT_LONG", {
                    "action": "ENTER",
                    "side": "LONG",
                    "reason": "CONFIRMED_KC_BREAKOUT_LONG",
                    "entry_atr": atr,
                }

            if side == "SHORT" and c_prev2["close"] < c_prev2.get("kc_lower", 0) and c_prev["close"] < c_prev.get("kc_lower", 0):
                return True, "CONFIRMED_KC_BREAKOUT_SHORT", {
                    "action": "ENTER",
                    "side": "SHORT",
                    "reason": "CONFIRMED_KC_BREAKOUT_SHORT",
                    "entry_atr": atr,
                }"""

content = content.replace(old_block_2, new_block_2)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
