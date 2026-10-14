"""
MacroCandleStructureGate
提供 AI 全域視角，透過通道相對位置、乖離率與 K 棒形態，
在開倉前進行強制結構性審查。
"""

class MacroCandleStructureGate:
    def __init__(self, kc_pos_long_max: float = 0.35, kc_pos_short_min: float = 0.65, max_bias_atr: float = 1.2):
        self.kc_pos_long_max = kc_pos_long_max    # 多單最高允許位置 (通道下 35%)
        self.kc_pos_short_min = kc_pos_short_min  # 空單最低允許位置 (通道上 65%)
        self.max_bias_atr = max_bias_atr          # 最大允許乖離倍數

    def can_open(self, side: str, closed_bar: dict, indicators: dict) -> tuple[bool, str]:
        """
        AI 開倉前置審查接口
        :param side: 'LONG' 或 'SHORT'
        :param closed_bar: 最近一根已確認收盤的 K 棒字典 (open, high, low, close, volume)
        :param indicators: 指標字典 (kc_upper, kc_lower, kc_middle, ma15, atr)
        :return: (is_allowed: bool, reason: str)
        """
        close = float(closed_bar['close'])
        open_price = float(closed_bar['open'])
        high = float(closed_bar['high'])
        low = float(closed_bar['low'])

        kc_upper = float(indicators['kc_upper'])
        kc_lower = float(indicators['kc_lower'])
        ma15 = float(indicators['ma15'])
        atr = float(indicators.get('atr', 1e-8))

        # 1. 計算通道相對位置百分比 (0.0 = 下軌, 1.0 = 上軌)
        kc_range = kc_upper - kc_lower
        kc_pos = (close - kc_lower) / kc_range if kc_range > 0 else 0.5

        # 2. 計算對 MA15 的乖離幅度 (以 ATR 為單位)
        bias_atr = abs(close - ma15) / (atr if atr > 0 else 1e-8)

        # 3. K 棒影線與實體計算
        body = abs(close - open_price)
        upper_wick = high - max(open_price, close)
        lower_wick = min(open_price, close) - low

        # =====================================================================
        # 多單審查（買多）
        # =====================================================================
        if side == 'LONG':
            # 規則 1：通道位置不可追高
            if kc_pos > self.kc_pos_long_max:
                return False, f"VETO_LONG: 價格位於通道偏高處 (KC_Pos: {kc_pos:.2f} > {self.kc_pos_long_max})，嚴禁追多"

            # 規則 2：不可在超買過度延伸時進場
            if bias_atr > self.max_bias_atr:
                return False, f"VETO_LONG: 乖離過大 (Bias: {bias_atr:.2f}x ATR)，嚴禁追多"

            # 規則 3：頂部遇阻上影線不可做多
            if upper_wick > body and close < high:
                return False, "VETO_LONG: K 棒受阻留長上影線，禁止開多"

            return True, "PASSED_MACRO_LONG_GATE"

        # =====================================================================
        # 空單審查（賣空）
        # =====================================================================
        elif side == 'SHORT':
            # 規則 1：通道位置不可在地板追空
            if kc_pos < self.kc_pos_short_min:
                return False, f"VETO_SHORT: 價格已跌入通道底部 (KC_Pos: {kc_pos:.2f} < {self.kc_pos_short_min})，嚴禁地板追空"

            # 規則 2：不可在超跌過度延伸時追空
            if bias_atr > self.max_bias_atr:
                return False, f"VETO_SHORT: 乖離過大 (Bias: {bias_atr:.2f}x ATR)，嚴禁追空"

            # 規則 3：底部爆量長下影線不可追空
            if lower_wick > body and close > low:
                return False, "VETO_SHORT: K 棒見底留長下影線，禁止追空"

            return True, "PASSED_MACRO_SHORT_GATE"

        return False, "UNKNOWN_SIDE"
