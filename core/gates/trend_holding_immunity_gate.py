"""Gate: TrendHoldingImmunityGate - 單邊趨勢全程護航閘門

徹底拔除單邊行情中的中途碎步平倉，達成不破關鍵均線全程死抱。
"""
from typing import Tuple, Dict, Any, Optional
import math


class TrendHoldingImmunityGate:
    """單邊趨勢全程護航閘門：

    凡任何出場邏輯（包含階梯鎖利、利潤回吐平倉、指標平倉等）欲發出平倉訊號前，
    必須強制通過此 Gate 審查。凡趨勢結構未破壞者，一律一票否決平倉（REJECT_EXIT）：

    1. 多單全程護航閘門（Long Holding Immunity - 未破 MA15 嚴禁平多）：
       - 檢核條件：
         * 均線維持多頭順向：ma15_slope >= 0 或 ma5 > ma15
         * 1m K 棒收盤價未實體摜破 MA15：close >= ma15
       - 處置動作：
         * 100% 絕對禁止放行任何平多指令（REJECT_EXIT: TREND_INTACT_ABOVE_MA15）！
         * 一票否決任何微幅利潤回吐、單根陰線回踩、碰觸 MA5 等中途過敏平倉。
       - 唯一平多放行條件：
         * 1m 收盤價實體摜破 MA15（close < ma15）或跌破 KC 中軌（close < kc_middle）。

    2. 空單全程護航閘門（Short Holding Immunity - 未站上 MA15 嚴禁平空）：
       - 檢核條件：
         * 均線維持空頭順向：ma15_slope <= 0 或 ma5 < ma15
         * 1m K 棒收盤價未實體突破站上 MA15：close <= ma15
       - 處置動作：
         * 100% 絕對禁止放行任何平空指令（REJECT_EXIT: TREND_INTACT_BELOW_MA15）！
         * 一票否決任何小陽線回抽、反彈碰觸 MA5、階梯浮盈回撤等中途過敏平倉。
       - 唯一平空放行條件：
         * 1m 收盤價實體站上 MA15（close > ma15）或突破 KC 中軌（close > kc_middle）。
    """

    def can_exit(
        self,
        position_side: str,
        bar: Dict[str, Any],
        indicators: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """審查當前持倉是否允許平倉。

        Args:
            position_side: 'LONG' or 'SHORT'
            bar: 包含 'close', 'open', 'high', 'low' 等當根 K 棒數據
            indicators: 包含 'ma5', 'ma15', 'ma15_slope', 可選 'kc_middle' 等指標
        Returns:
            (can_exit: bool, reason: str)
        """
        if position_side not in ('LONG', 'SHORT'):
            return True, "PASSED_TREND_GATE"

        close = float(bar['close'])
        ma5 = float(indicators['ma5'])
        ma15 = float(indicators['ma15'])
        ma15_slope = float(indicators.get('ma15_slope', 0.0))
        kc_middle = float(indicators.get('kc_middle', 0.0))

        # 1. 多單全程護航閘門（Long Holding Immunity - 未破 MA15 嚴禁平多）
        if position_side == 'LONG':
            # 唯一平多放行條件：1m 收盤價實體摜破 MA15（close < ma15）或跌破 KC 中軌（close < kc_middle）
            if close < ma15 or (kc_middle > 0 and close < kc_middle):
                return True, "PASSED_TREND_GATE"

            # 檢核條件：均線維持多頭順向 (ma15_slope >= 0 或 ma5 > ma15) 且 1m 收盤價未破 MA15 (close >= ma15)
            if close >= ma15 and (ma15_slope >= 0 or ma5 > ma15):
                return False, "REJECT_EXIT: TREND_INTACT_ABOVE_MA15 (VETO)"

        # 2. 空單全程護航閘門（Short Holding Immunity - 未站上 MA15 嚴禁平空）
        elif position_side == 'SHORT':
            # 唯一平空放行條件：1m 收盤價實體站上 MA15（close > ma15）或突破 KC 中軌（close > kc_middle）
            if close > ma15 or (kc_middle > 0 and close > kc_middle):
                return True, "PASSED_TREND_GATE"

            # 檢核條件：均線維持空頭順向 (ma15_slope <= 0 或 ma5 < ma15) 且 1m 收盤價未站上 MA15 (close <= ma15)
            if close <= ma15 and (ma15_slope <= 0 or ma5 < ma15):
                return False, "REJECT_EXIT: TREND_INTACT_BELOW_MA15 (VETO)"

        return True, "PASSED_TREND_GATE"
