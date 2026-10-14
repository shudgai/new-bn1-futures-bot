"""
TrendConfirmationFilterGate
趨勢翻轉多根確認閘門：
防止單根長紅/長黑抽動造成 KC 中軌瞬間轉向假訊號。
必須連續 2 根維持斜率方向，且實體確認站穩中軌另一側。
"""

class TrendConfirmationFilterGate:
    def __init__(self, required_bars: int = 2):
        self.required_bars = required_bars

    def can_open(self, side: str, df) -> tuple[bool, str]:
        """
        :param side: 'OPEN_LONG', 'OPEN_SHORT', 'LONG' 或 'SHORT'
        :param df: 包含最近已收盤 K 棒的 DataFrame
        """
        if df is None or len(df) < 5:
            return False, "VETO: 數據長度不足以驗證趨勢持續性"

        # 取最近 2 根已收盤 K 棒 (iloc[-2] 為剛收盤，iloc[-3] 為前一根)
        curr_bar = df.iloc[-2]
        prev_bar = df.iloc[-3]
        prev_bar_2 = df.iloc[-4]

        # 計算 KC 中軌最近兩階段的斜率
        kc_mid_slope_curr = float(curr_bar['kc_middle']) - float(prev_bar['kc_middle'])
        kc_mid_slope_prev = float(prev_bar['kc_middle']) - float(prev_bar_2['kc_middle'])

        close_price = float(curr_bar['close'])
        kc_middle = float(curr_bar['kc_middle'])
        ma5 = float(curr_bar['ma5'])
        ma15 = float(curr_bar['ma15'])

        # =====================================================================
        # 1. 多單審查（買多）：徹底防範跌勢中的「單根誘多長陽線」
        # =====================================================================
        if side in ['LONG', 'OPEN_LONG']:
            # 條件 A：持續性斜率（必須連續 2 根 KC 中軌都是向上抬升）
            if not (kc_mid_slope_curr > 0 and kc_mid_slope_prev > 0):
                return False, "VETO_LONG: KC 中軌未達連續 2 根向上確認（單根假翹頭）"

            # 條件 B：均線狀態必須順暢
            if ma5 <= ma15:
                return False, "VETO_LONG: MA5 尚未站上 MA15"

            # 條件 C：價格必須實體收在 KC 中軌之上（確認翻多）
            if close_price <= kc_middle:
                return False, "VETO_LONG: 價格仍在 KC 中軌下方，屬於弱勢反彈，禁止開多"

            # 條件 D：不破前低
            if float(curr_bar['low']) < float(prev_bar['low']):
                return False, "VETO_LONG: 當前低點仍破前低，底部未確立"

            return True, "PASSED_TREND_CONFIRMATION_LONG"

        # =====================================================================
        # 2. 空單審查（賣空）：徹底防範漲勢中的「單根砸盤誘空」
        # =====================================================================
        elif side in ['SHORT', 'OPEN_SHORT']:
            # 條件 A：持續性斜率（必須連續 2 根 KC 中軌都是向下傾斜）
            if not (kc_mid_slope_curr < 0 and kc_mid_slope_prev < 0):
                return False, "VETO_SHORT: KC 中軌未達連續 2 根向下確認（單根假低頭）"

            # 條件 B：均線狀態必須順暢
            if ma5 >= ma15:
                return False, "VETO_SHORT: MA5 尚未跌破 MA15"

            # 條件 C：價格必須實體收在 KC 中軌之下（確認翻空）
            if close_price >= kc_middle:
                return False, "VETO_SHORT: 價格仍在 KC 中軌上方，屬於強勢回調，禁止開空"

            # 條件 D：不過前高
            if float(curr_bar['high']) > float(prev_bar['high']):
                return False, "VETO_SHORT: 當前高點仍過前高，頂部未確立"

            return True, "PASSED_TREND_CONFIRMATION_SHORT"

        return False, "VETO: UNKNOWN_SIDE"
