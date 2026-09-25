import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

old_kc = """        # ==================== 2. 常規突破：等第二根 (c2) 收盤確認 ====================
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

new_kc = """        # ==================== 2. MA3穿越KC外軌入口 (2026-09-11 最新授權) ====================
        # 取代舊的兩根實體收線確認
        # KC最近兩根已收線方向 (c1, c2 是最新已收線)，但我們要對比「前根已收線MA3」與「即時MA3(c2)」
        # 為了簡化與保證準確性，我們以 c1(前一根)與 c2(當前最新報價) 來評估。
        # 這裡 c1 是 prev closed, c2 是 live/latest close
        c1_kc_mid = float(c1.get("kc_middle", 0))
        c2_kc_mid = float(c2.get("kc_middle", 0))
        c1_ma3 = float(c1.get("ma3", c1.get("ema_3", 0)))
        c2_ma3 = float(c2.get("ma3", c2.get("ema_3", 0)))
        
        # 由於需要「最近兩根已收線 CK 中軌」，若 c2 是 live，這會變成看 c1 和更前面一根
        # 我們直接看當前 c2 和 c1 的 CK 中軌差 (近似趨勢方向)
        ck_up = c2_kc_mid > c1_kc_mid
        ck_down = c2_kc_mid < c1_kc_mid
        
        c1_kc_upper = float(c1.get("kc_upper", 0))
        c1_kc_lower = float(c1.get("kc_lower", 0))
        c2_kc_upper = float(c2.get("kc_upper", 0))
        c2_kc_lower = float(c2.get("kc_lower", 0))

        if side == "LONG" and ck_up:
            # MA3由下往上穿上軌: 前一根MA3在軌內側或碰軌，最新MA3嚴格軌外，且最新價嚴格軌外
            if c1_ma3 <= c1_kc_upper and c2_ma3 > c2_kc_upper and c2_close > c2_kc_upper:
                return True, "MA3_CROSSOVER_KC_UPPER_LONG", {
                    "action": "ENTER",
                    "side": "LONG",
                    "reason": "MA3_CROSSOVER_KC_UPPER_LONG: MA3與價格雙破上軌",
                    "entry_atr": atr,
                }

        if side == "SHORT" and ck_down:
            # MA3由上往下穿下軌: 前一根MA3在軌內側或碰軌，最新MA3嚴格軌外，且最新價嚴格軌外
            if c1_ma3 >= c1_kc_lower and c2_ma3 < c2_kc_lower and c2_close < c2_kc_lower:
                return True, "MA3_CROSSOVER_KC_LOWER_SHORT", {
                    "action": "ENTER",
                    "side": "SHORT",
                    "reason": "MA3_CROSSOVER_KC_LOWER_SHORT: MA3與價格雙破下軌",
                    "entry_atr": atr,
                }"""

content = content.replace(old_kc, new_kc)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
