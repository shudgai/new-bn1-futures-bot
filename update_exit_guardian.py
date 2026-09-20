import re

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

new_evaluate_exit = """    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame,
                      current_price: float, **kwargs) -> Optional[str]:
        if frame is None or len(frame) < 4:
            return None

        side        = position.get("side", "LONG")
        entry_price = float(position.get("entry_price", 0.0))
        symbol      = position.get("symbol", "")
        if entry_price <= 0:
            return None

        curr   = frame.iloc[-1]
        prev_1 = frame.iloc[-2]
        prev_2 = frame.iloc[-3]
        prev_3 = frame.iloc[-4]

        atr = float(prev_1.get("atr", 1.0))
        if atr <= 0:
            atr = entry_price * 0.01

        # 若尚未初始化，則進行初始化
        if "active_stop_price" not in position:
            self.initialize_position(position, entry_price, atr)
        
        active_stop = position.get("active_stop_price", position.get("defense_line", entry_price))
        
        # ══════════════════════════════════════════════════════════════
        # 優先級 1：極端風險防禦 (硬停損 1.5 ATR - 盤中即時觸發)
        # ══════════════════════════════════════════════════════════════
        if side == "LONG" and current_price <= active_stop:
            logger.warning(f"[EXIT_HARD_STOP] LONG hit 1.5 ATR stop @ {current_price:.6f}")
            return "EXIT_HARD_STOP_1.5_ATR"
        if side == "SHORT" and current_price >= active_stop:
            logger.warning(f"[EXIT_HARD_STOP] SHORT hit 1.5 ATR stop @ {current_price:.6f}")
            return "EXIT_HARD_STOP_1.5_ATR"

        # =====================================================================
        # 以下所有邏輯，僅在「有新的 K 棒收盤時」才進行評估 (Close-only Check)
        # =====================================================================
        bar_id = prev_1.name if hasattr(prev_1, "name") else str(prev_1.to_dict())
        if position.get("last_evaluated_closed_bar_id") == bar_id:
            return None  # 盤中跳動，忽略
            
        # 記錄已評估的收盤 K 棒
        position["last_evaluated_closed_bar_id"] = bar_id

        curr_close = float(prev_1["close"])
        kc_upper = float(prev_1.get("kc_upper", curr_close))
        kc_lower = float(prev_1.get("kc_lower", curr_close))
        
        prev1_open = float(prev_1["open"])
        prev2_open = float(prev_2["open"])
        prev2_close = float(prev_2["close"])
        
        prev1_is_green = curr_close > prev1_open
        prev1_is_red = curr_close < prev1_open
        prev2_is_green = prev2_close > prev2_open
        prev2_is_red = prev2_close < prev2_open
        
        prev1_body = abs(curr_close - prev1_open)
        prev2_body = abs(prev2_close - prev2_open)

        # ══════════════════════════════════════════════════════════════
        # 優先級 1：極端風險防禦 (大瀑布 / 連續異常)
        # ══════════════════════════════════════════════════════════════
        is_waterfall = prev1_body >= 3.0 * atr
        
        # 判斷是否為反向的連續異常
        if side == "LONG":
            prev1_is_reverse = prev1_is_red
            prev2_is_reverse = prev2_is_red
        else:
            prev1_is_reverse = prev1_is_green
            prev2_is_reverse = prev2_is_green
            
        is_consecutive_extreme = (prev1_body >= 1.5 * atr) and (prev2_body >= 1.5 * atr) and prev1_is_reverse and prev2_is_reverse
        
        if is_waterfall or is_consecutive_extreme:
            logger.warning(f"[EXIT_EXTREME_RISK_MELTDOWN] {side} hit meltdown protection @ {curr_close:.6f}")
            return "EXIT_EXTREME_RISK_MELTDOWN"

        # ══════════════════════════════════════════════════════════════
        # 優先級 2：動態獲利收割 (移動止盈)
        # ══════════════════════════════════════════════════════════════
        if side == "LONG":
            unrealized_profit_atr = (curr_close - entry_price) / atr
        else:
            unrealized_profit_atr = (entry_price - curr_close) / atr
            
        max_profit_atr = position.get("max_profit_atr", 0.0)
        max_profit_atr = max(max_profit_atr, unrealized_profit_atr)
        position["max_profit_atr"] = max_profit_atr

        # 啟動門檻：最高浮盈達到 0.7 ATR
        if max_profit_atr >= 0.7:
            locked_profit_atr = max_profit_atr - 0.7
            if unrealized_profit_atr <= locked_profit_atr:
                logger.warning(f"[EXIT_DYNAMIC_PROFIT_HARVEST] {side} profit dropped to {unrealized_profit_atr:.2f} ATR (locked: {locked_profit_atr:.2f} ATR) @ {curr_close:.6f}")
                return "EXIT_DYNAMIC_PROFIT_HARVEST"

        # ══════════════════════════════════════════════════════════════
        # 優先級 3：結構性反轉 (站回異側軌道內 + 連續反向K)
        # ══════════════════════════════════════════════════════════════
        if side == "LONG":
            # 多單：跌破下軌內部 + 連2紅
            if curr_close < kc_lower and (prev1_is_red and prev2_is_red):
                logger.warning(f"[EXIT_STRUCTURAL_REVERSAL] LONG structural reversal @ {curr_close:.6f}")
                return "EXIT_STRUCTURAL_REVERSAL"
        elif side == "SHORT":
            # 空單：站上上軌內部 + 連2綠
            if curr_close > kc_upper and (prev1_is_green and prev2_is_green):
                logger.warning(f"[EXIT_STRUCTURAL_REVERSAL] SHORT structural reversal @ {curr_close:.6f}")
                return "EXIT_STRUCTURAL_REVERSAL"

        # ══════════════════════════════════════════════════════════════
        # 優先級 4：結構性峰值收割 (MA3轉向 + 站回同側軌道內 + MA15轉向)
        # ══════════════════════════════════════════════════════════════
        ma3_prev1 = float(prev_1.get("ma3", prev_1.get("ema_3", 0.0)))
        ma3_prev2 = float(prev_2.get("ma3", prev_2.get("ema_3", 0.0)))
        ma15_prev1 = float(prev_1.get("ma15", 0.0))
        ma15_prev2 = float(prev_2.get("ma15", 0.0))
        
        min_turn_threshold = atr * 0.10
        ma3_turning_down = (ma3_prev2 - ma3_prev1) > min_turn_threshold
        ma3_turning_up = (ma3_prev1 - ma3_prev2) > min_turn_threshold
        ma15_turning_down = (ma15_prev2 - ma15_prev1) > min_turn_threshold
        ma15_turning_up = (ma15_prev1 - ma15_prev2) > min_turn_threshold
        
        if side == "LONG":
            # 站回上軌內 = curr_close <= kc_upper
            if ma3_turning_down and (curr_close <= kc_upper) and ma15_turning_down:
                logger.warning(f"[EXIT_TRUE_PEAK_REVERSAL] LONG true peak reversal @ {curr_close:.6f}")
                return "EXIT_TRUE_PEAK_REVERSAL"
        elif side == "SHORT":
            # 站回下軌內 = curr_close >= kc_lower
            if ma3_turning_up and (curr_close >= kc_lower) and ma15_turning_up:
                logger.warning(f"[EXIT_TRUE_PEAK_REVERSAL] SHORT true peak reversal @ {curr_close:.6f}")
                return "EXIT_TRUE_PEAK_REVERSAL"

        return None"""

# Use regex to replace the function
pattern = re.compile(r'    def evaluate_exit\(self, position: Dict\[str, Any\], frame: pd\.DataFrame,\n                      current_price: float, \*\*kwargs\) -> Optional\[str\]:.*?(?=    def handle_post_exit_cleanup)', re.DOTALL)
new_content = pattern.sub(new_evaluate_exit + "\n", content)

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(new_content)
print("Updated successfully")
