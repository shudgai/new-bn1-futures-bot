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
        # 層級一：絕對保命 (1.5 ATR 硬停損)
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

        # 實體過濾 (Body Weight Filter >= 0.3 ATR)
        prev_1_body = abs(float(prev_1["close"]) - float(prev_1["open"]))
        prev_2_body = abs(float(prev_2["close"]) - float(prev_2["open"]))
        prev_1_is_valid_reverse = (prev_1_body >= 0.3 * atr)
        prev_2_is_valid_reverse = (prev_2_body >= 0.3 * atr)

        # 取代 curr，以下全使用 prev_1 作為當前評估基準
        curr_close = float(prev_1["close"])
        
        # 指標準備
        ma5 = float(prev_1.get("ma5", prev_1.get("ema_5", curr_close)))
        kc_upper = float(prev_1.get("kc_upper", curr_close))
        kc_lower = float(prev_1.get("kc_lower", curr_close))
        
        curr_is_green = float(prev_1["close"]) > float(prev_1["open"])
        curr_is_red = float(prev_1["close"]) < float(prev_1["open"])
        prev2_is_green = float(prev_2["close"]) > float(prev_2["open"])
        prev2_is_red = float(prev_2["close"]) < float(prev_2["open"])
        
        # 實體重心防禦 (超過前一根實體 60%)
        heavy_body_break = (prev_1_body >= prev_2_body * 0.6)
        
        # ══════════════════════════════════════════════════════════════
        # 決策樹層級 2：強勢區護航 (Trend Buffer) & 實體重心防禦
        # ══════════════════════════════════════════════════════════════
        if side == "LONG":
            is_outside_kc = curr_close > kc_upper
            back_inside_kc = curr_close <= kc_upper
            consecutive_2_red = curr_is_red and prev2_is_red
            ma5_broken = curr_close < ma5
            
            if is_outside_kc:
                if (back_inside_kc or consecutive_2_red) and heavy_body_break and prev_1_is_valid_reverse:
                    logger.warning(f"[EXIT_TREND_BUFFER_BREAK] LONG trend buffer broken @ {curr_close:.6f}")
                    return "EXIT_TREND_BUFFER_BREAK"
            else:
                if ma5_broken and heavy_body_break and curr_is_red and prev_1_is_valid_reverse:
                    logger.warning(f"[EXIT_MA5_BODY_BREAK] LONG body fell below MA5 @ {curr_close:.6f}")
                    return "EXIT_MA5_BODY_BREAK"
                    
        elif side == "SHORT":
            is_outside_kc = curr_close < kc_lower
            back_inside_kc = curr_close >= kc_lower
            consecutive_2_green = curr_is_green and prev2_is_green
            ma5_broken = curr_close > ma5
            
            if is_outside_kc:
                if (back_inside_kc or consecutive_2_green) and heavy_body_break and prev_1_is_valid_reverse:
                    logger.warning(f"[EXIT_TREND_BUFFER_BREAK] SHORT trend buffer broken @ {curr_close:.6f}")
                    return "EXIT_TREND_BUFFER_BREAK"
            else:
                if ma5_broken and heavy_body_break and curr_is_green and prev_1_is_valid_reverse:
                    logger.warning(f"[EXIT_MA5_BODY_BREAK] SHORT body rose above MA5 @ {curr_close:.6f}")
                    return "EXIT_MA5_BODY_BREAK"

        # ══════════════════════════════════════════════════════════════
        # 三段式動態利潤護航 (Dynamic Profit Guard)
        # ══════════════════════════════════════════════════════════════
        if side == "LONG":
            unrealized_profit_atr = (curr_close - entry_price) / atr
        else:
            unrealized_profit_atr = (entry_price - curr_close) / atr
            
        max_profit_atr = position.get("max_profit_atr", 0.0)
        max_profit_atr = max(max_profit_atr, unrealized_profit_atr)
        position["max_profit_atr"] = max_profit_atr

        # Stage 1: Cost Defense (max_profit_atr >= 0.8)
        if max_profit_atr >= 0.8:
            if side == "LONG":
                active_stop = max(active_stop, entry_price)
            else:
                active_stop = min(active_stop, entry_price)
            position["active_stop_price"] = active_stop

        # Stage 2: Swing Guard (1.0 < max_profit_atr < 2.0)
        if 1.0 < max_profit_atr < 2.0:
            swing_frame = frame.iloc[-5:-2] # 前面的 K 棒
            if side == "LONG":
                swing_low = float(swing_frame['low'].min())
                if curr_close < swing_low and prev_1_is_valid_reverse:
                    logger.warning(f"[EXIT_SWING_GUARD] LONG hit swing low {swing_low:.6f} @ {curr_close:.6f}")
                    return "EXIT_SWING_GUARD"
            else:
                swing_high = float(swing_frame['high'].max())
                if curr_close > swing_high and prev_1_is_valid_reverse:
                    logger.warning(f"[EXIT_SWING_GUARD] SHORT hit swing high {swing_high:.6f} @ {curr_close:.6f}")
                    return "EXIT_SWING_GUARD"

        # Stage 3: Big Meat Harvest (max_profit_atr >= 2.0)
        if max_profit_atr >= 2.0:
            locked_profit_atr = max_profit_atr * 0.70
            if unrealized_profit_atr <= locked_profit_atr and prev_1_is_valid_reverse:
                # Double Confirmation for Chandelier Lock
                prev2_unrealized_profit_atr = (prev_2["close"] - entry_price) / atr if side == "LONG" else (entry_price - prev_2["close"]) / atr
                if prev2_unrealized_profit_atr <= locked_profit_atr and prev_2_is_valid_reverse:
                    logger.warning(f"[EXIT_CHANDELIER_LOCK] {side} profit dropped to {unrealized_profit_atr:.2f} ATR (locked: {locked_profit_atr:.2f} ATR) @ {curr_close:.6f}")
                    return "EXIT_CHANDELIER_LOCK"

        # ══════════════════════════════════════════════════════════════
        # 層級三：真實峰谷平倉 (True Peak Exit) - 結構導向 (Double Confirmation)
        # ══════════════════════════════════════════════════════════════
        def check_peak_exit(eval_bar, prev_bar, side, atr):
            ma3_eval = float(eval_bar.get("ma3", eval_bar.get("ema_3", 0.0)))
            ma3_prev = float(prev_bar.get("ma3", prev_bar.get("ema_3", 0.0)))
            ma15_eval = float(eval_bar.get("ma15", 0.0))
            ma15_prev = float(prev_bar.get("ma15", 0.0))
            rsi_eval = float(eval_bar.get("rsi", 50.0))
            kc_upper = float(eval_bar.get("kc_upper", 0.0))
            kc_lower = float(eval_bar.get("kc_lower", 0.0))
            
            min_turn_threshold = atr * 0.10
            ma3_turning_down = (ma3_prev - ma3_eval) > min_turn_threshold
            ma3_turning_up = (ma3_eval - ma3_prev) > min_turn_threshold
            ma15_turning_down = (ma15_prev - ma15_eval) > min_turn_threshold
            ma15_turning_up = (ma15_eval - ma15_prev) > min_turn_threshold
            
            c_close = float(eval_bar["close"])
            
            # 從 eval_bar 往前取 3 根作為 recent_3_bars (不含 eval_bar)
            # 因為 eval_bar 是 prev_1 時，recent_3 是 prev_2, prev_3, prev_4
            idx = list(frame.index).index(eval_bar.name) if hasattr(eval_bar, "name") else -1
            if idx >= 3:
                recent_3_bars = frame.iloc[idx-3:idx]
                recent_3_high = float(recent_3_bars['high'].max())
                recent_3_low = float(recent_3_bars['low'].min())
            else:
                recent_3_high = c_close
                recent_3_low = c_close

            if side == "LONG":
                if ma3_turning_down:
                    is_structural_reversal = (c_close <= kc_upper) or (c_close < recent_3_low)
                    is_trend_reversal = ma15_turning_down
                    is_momentum_exhausted = (rsi_eval > 75)
                    if is_structural_reversal or is_trend_reversal or is_momentum_exhausted:
                        return True
            elif side == "SHORT":
                if ma3_turning_up:
                    is_structural_reversal = (c_close >= kc_lower) or (c_close > recent_3_high)
                    is_trend_reversal = ma15_turning_up
                    is_momentum_exhausted = (rsi_eval < 25)
                    if is_structural_reversal or is_trend_reversal or is_momentum_exhausted:
                        return True
            return False

        if len(frame) >= 5:
            prev_1_peak = check_peak_exit(prev_1, prev_2, side, atr)
            prev_2_peak = check_peak_exit(prev_2, prev_3, side, atr)
            
            if prev_1_peak and prev_2_peak and prev_1_is_valid_reverse:
                logger.warning(f"[LOG]: Exit Type: TRUE_PEAK | Double Confirmed on prev_1 and prev_2 @ {curr_close:.6f}")
                return "EXIT_TRUE_PEAK_REVERSAL"

        return None"""

# Use regex to replace the function
pattern = re.compile(r'    def evaluate_exit\(self, position: Dict\[str, Any\], frame: pd\.DataFrame,\n                      current_price: float, \*\*kwargs\) -> Optional\[str\]:.*?(?=    def handle_post_exit_cleanup)', re.DOTALL)
new_content = pattern.sub(new_evaluate_exit + "\n", content)

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(new_content)
print("Updated successfully")
