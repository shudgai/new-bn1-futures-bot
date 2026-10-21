"""
FiveLinesConfluenceGate
五線同向共振審查閘門（含通道喇叭口擴張防卡死與 KC %B 頂底邊界防護）：
嚴格檢驗 MA5、MA15、KC 上軌、KC 中軌、KC 下軌是否朝同向傾斜發散。
- 支援單邊強勢行情下的 KC 帶寬擴張（喇叭口張開）：
  * 空單暴跌時：若 KC 帶寬擴張且中軌與下軌向下，允許上軌因 ATR 放大而向上翹起，不誤判逆向。
  * 多單暴漲時：若 KC 帶寬擴張且中軌與上軌向上，允許下軌因 ATR 放大而被往下拉，不誤判逆向。
- 通道相對位置 (KC %B) 頂底保護：
  * 空單保護：KC_Pos < 0.20 視為地板追空，強制一票否決。
  * 多單保護：KC_Pos > 0.80 視為天花板追多，強制一票否決。
- 收盤 K 棒驗證：
  * 嚴格確保只傳入已走完定型的收盤 K 棒（is_closed == True）。
"""

class FiveLinesConfluenceGate:
    def __init__(
        self,
        min_slope_pct: float = 0.0002,
        kc_pos_long_max: float = 0.80,
        kc_pos_short_min: float = 0.20,
        max_overshoot_atr: float = 2.0
    ):
        self.min_slope_pct = min_slope_pct
        self.kc_pos_long_max = kc_pos_long_max
        self.kc_pos_short_min = kc_pos_short_min
        self.max_overshoot_atr = max_overshoot_atr

    def can_open(
        self,
        side: str,
        bar: dict,
        prev_bar: dict,
        indicators: dict,
        prev_indicators: dict,
        entry_pattern: str | None = None,
        entry_mode: str | None = None,
    ) -> tuple[bool, str]:
        """
        五線同向共振審查接口
        :param side: 'LONG' 或 'SHORT'
        :param bar: 當前 K 棒字典 (close, open, high, low, is_closed)
        :param prev_bar: 前一根 K 棒字典
        :param indicators: 當前指標字典 (ma5, ma15, kc_middle, kc_upper, kc_lower, atr)
        :param prev_indicators: 前一根指標字典 (ma5, ma15, kc_middle, kc_upper, kc_lower)
        :return: (is_allowed: bool, reason: str)
        """
        # 0. 確保只傳入已收盤定型 K 棒
        if bar.get('is_closed') is False:
            return False, "VETO: 拒絕盤中未收盤 K 棒，必須為已定型 K 棒"

        price = float(bar['close'])
        atr = float(indicators.get('atr', 1e-8))

        # 1. 取得最新指標
        ma5_curr = float(indicators['ma5'])
        ma15_curr = float(indicators['ma15'])
        kc_mid_curr = float(indicators['kc_middle'])
        kc_upper_curr = float(indicators['kc_upper'])
        kc_lower_curr = float(indicators['kc_lower'])

        # 2. 取得前一根指標
        ma5_prev = float(prev_indicators['ma5'])
        ma15_prev = float(prev_indicators['ma15'])
        kc_mid_prev = float(prev_indicators['kc_middle'])
        kc_upper_prev = float(prev_indicators['kc_upper'])
        kc_lower_prev = float(prev_indicators['kc_lower'])

        # 3. 計算 5 條線的斜率
        slope_ma5 = (ma5_curr - ma5_prev) / price
        slope_ma15 = (ma15_curr - ma15_prev) / price
        slope_kc_mid = (kc_mid_curr - kc_mid_prev) / price
        slope_kc_upper = (kc_upper_curr - kc_upper_prev) / price
        slope_kc_lower = (kc_lower_curr - kc_lower_prev) / price

        # 4. 通道相對位置 (KC %B) 與帶寬 (Bandwidth)
        curr_bw = kc_upper_curr - kc_lower_curr
        prev_bw = kc_upper_prev - kc_lower_prev
        is_bandwidth_expanding = curr_bw > prev_bw

        kc_pos = (price - kc_lower_curr) / curr_bw if curr_bw > 0 else 0.5

        # =====================================================================
        # 多單審查
        # =====================================================================
        if side == 'LONG':
            # 通道位置天花板防護 (KC %B)：僅限通道內震盪或 MA_CROSSOVER 模式
            # 破軌模式價格自然在軌外 (KC_Pos >= 1.0)，改由外軌乖離不得超過 0.5 ATR 獨立把關
            is_breakout_mode = (
                (entry_pattern and any(b in str(entry_pattern).upper() for b in ("BREAKOUT", "CONTINUATION", "OUTSIDE", "BURST")))
                or (entry_mode and any(b in str(entry_mode).upper() for b in ("BREAKOUT", "CONTINUATION", "WILD_SWING")))
                or price > kc_upper_curr
            )
            if not is_breakout_mode and kc_pos > self.kc_pos_long_max:
                return False, f"VETO_LONG: 價格貼近天花板極限 (KC_Pos: {kc_pos:.2f} > {self.kc_pos_long_max:.2f})，拒絕天花板追多"

            if ma5_curr <= ma15_curr:
                return False, "VETO_LONG: MA5 尚未大於 MA15"

            if slope_ma5 <= self.min_slope_pct:
                return False, f"VETO_LONG: MA5 走平或向下 (slope: {slope_ma5:.6f})"
            if slope_ma15 <= self.min_slope_pct:
                return False, f"VETO_LONG: MA15 走平或向下 (slope: {slope_ma15:.6f})"
            if slope_kc_mid <= self.min_slope_pct:
                return False, f"VETO_LONG: KC中軌 走平或向下 (slope: {slope_kc_mid:.6f})"
            if slope_kc_upper <= self.min_slope_pct:
                return False, f"VETO_LONG: KC上軌 走平未打開 (slope: {slope_kc_upper:.6f})"

            # 【重要修正：允許通道擴張】：若帶寬擴張且中軌與上軌向上，允許下軌因 ATR 放大而被往下拉
            if slope_kc_lower <= self.min_slope_pct:
                can_expand_down = (
                    is_bandwidth_expanding
                    and slope_kc_mid > self.min_slope_pct
                    and slope_kc_upper > self.min_slope_pct
                )
                if not can_expand_down:
                    return False, f"VETO_LONG: KC下軌 走平或向下 (slope: {slope_kc_lower:.6f})"

            # 額外超標防護 (破軌模式不得超過 0.5 ATR)
            max_overshoot = 0.5 if is_breakout_mode else self.max_overshoot_atr
            if price > kc_upper_curr and (price - kc_upper_curr) > max_overshoot * atr:
                return False, f"VETO_LONG: 突破 KC 上軌過遠 (超標: {(price - kc_upper_curr)/atr:.2f} ATR > {max_overshoot} ATR)"

            return True, "PASSED_FIVE_LINES_LONG"

        # =====================================================================
        # 空單審查
        # =====================================================================
        elif side == 'SHORT':
            # 通道位置地板防護 (KC %B)：僅限通道內震盪或 MA_CROSSOVER 模式
            # 跌破外軌模式改由外軌乖離不得超過 0.5 ATR 獨立把關
            is_breakdown_mode = (
                (entry_pattern and any(b in str(entry_pattern).upper() for b in ("BREAKOUT", "CONTINUATION", "OUTSIDE", "BURST", "BREAKDOWN")))
                or (entry_mode and any(b in str(entry_mode).upper() for b in ("BREAKOUT", "CONTINUATION", "WILD_SWING")))
                or price < kc_lower_curr
            )
            if not is_breakdown_mode and kc_pos < self.kc_pos_short_min:
                return False, f"VETO_SHORT: 價格貼近地板極限 (KC_Pos: {kc_pos:.2f} < {self.kc_pos_short_min:.2f})，拒絕地板追空"

            if ma5_curr >= ma15_curr:
                return False, "VETO_SHORT: MA5 尚未小於 MA15"

            if slope_ma5 >= -self.min_slope_pct:
                return False, f"VETO_SHORT: MA5 走平或向上 (slope: {slope_ma5:.6f})"
            if slope_ma15 >= -self.min_slope_pct:
                return False, f"VETO_SHORT: MA15 走平或向上 (slope: {slope_ma15:.6f})"
            if slope_kc_mid >= -self.min_slope_pct:
                return False, f"VETO_SHORT: KC中軌 走平或向上 (slope: {slope_kc_mid:.6f})"
            if slope_kc_lower >= -self.min_slope_pct:
                return False, f"VETO_SHORT: KC下軌 走平未打開 (slope: {slope_kc_lower:.6f})"

            # 【重要修正：允許通道擴張】：若帶寬擴張且中軌與下軌向下，允許上軌因 ATR 放大而向上張開翹起
            if slope_kc_upper >= -self.min_slope_pct:
                can_expand_up = (
                    is_bandwidth_expanding
                    and slope_kc_mid < -self.min_slope_pct
                    and slope_kc_lower < -self.min_slope_pct
                )
                if not can_expand_up:
                    return False, f"VETO_SHORT: KC上軌 走平或向上 (slope: {slope_kc_upper:.6f})"

            # 額外超標防護 (跌破模式不得超過 0.5 ATR)
            max_overshoot = 0.5 if is_breakdown_mode else self.max_overshoot_atr
            if price < kc_lower_curr and (kc_lower_curr - price) > max_overshoot * atr:
                return False, f"VETO_SHORT: 跌破 KC 下軌過遠 (超標: {(kc_lower_curr - price)/atr:.2f} ATR > {max_overshoot} ATR)"

            return True, "PASSED_FIVE_LINES_SHORT"

        return False, "VETO: UNKNOWN_SIDE"
