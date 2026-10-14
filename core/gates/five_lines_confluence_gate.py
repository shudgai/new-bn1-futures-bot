"""
FiveLinesConfluenceGate
五線同向共振審查閘門：
嚴格檢驗 MA5、MA15、KC 上軌、KC 中軌、KC 下軌是否全數朝同一個方向傾斜發散。
任一線走平或反向，一律行使一票否決權（VETO）。
"""

class FiveLinesConfluenceGate:
    def __init__(self, min_slope_pct: float = 0.0002, max_overshoot_atr: float = 2.0):
        self.min_slope_pct = min_slope_pct
        self.max_overshoot_atr = max_overshoot_atr

    def can_open(
        self,
        side: str,
        bar: dict,
        prev_bar: dict,
        indicators: dict,
        prev_indicators: dict
    ) -> tuple[bool, str]:
        """
        五線同向共振審查接口
        :param side: 'LONG' 或 'SHORT'
        :param bar: 當前 K 棒字典 (close, open, high, low)
        :param prev_bar: 前一根 K 棒字典
        :param indicators: 當前指標字典 (ma5, ma15, kc_middle, kc_upper, kc_lower, atr)
        :param prev_indicators: 前一根指標字典 (ma5, ma15, kc_middle, kc_upper, kc_lower)
        :return: (is_allowed: bool, reason: str)
        """
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

        # =====================================================================
        # 多單審查：五線必須全部向上傾斜 (> min_slope_pct)
        # =====================================================================
        if side == 'LONG':
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
            if slope_kc_lower <= self.min_slope_pct:
                return False, f"VETO_LONG: KC下軌 走平或向下 (slope: {slope_kc_lower:.6f})"

            # 通道極值防護（防天花板追多）
            if price > kc_upper_curr and (price - kc_upper_curr) > self.max_overshoot_atr * atr:
                return False, f"VETO_LONG: 突破 KC 上軌過遠 (超標: {(price - kc_upper_curr)/atr:.2f} ATR)"

            return True, "PASSED_FIVE_LINES_LONG"

        # =====================================================================
        # 空單審查：五線必須全部向下傾斜 (< -min_slope_pct)
        # =====================================================================
        elif side == 'SHORT':
            if ma5_curr >= ma15_curr:
                return False, "VETO_SHORT: MA5 尚未小於 MA15"

            if slope_ma5 >= -self.min_slope_pct:
                return False, f"VETO_SHORT: MA5 走平或向上 (slope: {slope_ma5:.6f})"
            if slope_ma15 >= -self.min_slope_pct:
                return False, f"VETO_SHORT: MA15 走平或向上 (slope: {slope_ma15:.6f})"
            if slope_kc_mid >= -self.min_slope_pct:
                return False, f"VETO_SHORT: KC中軌 走平或向上 (slope: {slope_kc_mid:.6f})"
            if slope_kc_upper >= -self.min_slope_pct:
                return False, f"VETO_SHORT: KC上軌 走平或向上 (slope: {slope_kc_upper:.6f})"
            if slope_kc_lower >= -self.min_slope_pct:
                return False, f"VETO_SHORT: KC下軌 走平未打開 (slope: {slope_kc_lower:.6f})"

            # 通道極值防護（防地板追空）
            if price < kc_lower_curr and (kc_lower_curr - price) > self.max_overshoot_atr * atr:
                return False, f"VETO_SHORT: 跌破 KC 下軌過遠 (超標: {(kc_lower_curr - price)/atr:.2f} ATR)"

            return True, "PASSED_FIVE_LINES_SHORT"

        return False, "VETO: UNKNOWN_SIDE"
