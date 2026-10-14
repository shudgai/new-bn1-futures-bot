"""Gate: TrendHoldingImmunityGate - 單邊趨勢全程護航閘門

徹底拔除單邊行情中的中途碎步平倉（包含十字星、反向 K 棒、影線滯漲、MA5 回踩等），達成不破關鍵均線全程死抱。
"""
from typing import Tuple, Dict, Any, Optional
import math


class TrendHoldingImmunityGate:
    """單邊趨勢全程護航閘門：

    凡任何出場邏輯（包含十字星、反向K、階梯鎖利、利潤回吐平倉、指標平倉等）欲發出平倉訊號前，
    必須強制通過此 Gate 審查。凡趨勢結構未破壞者，一律一票否決平倉（REJECT_EXIT）：

    1. 硬停損保護 (最高級別放行: 包含 -1.5% 絕對硬止損)
    2. 淨利達到 10% 啟動動態鎖利 (且回吐 20%) 放行
    3. 多單全程護航閘門（Long Holding Immunity - 未破 MA15 嚴禁平多）：
       - 只要 1m 收盤價未實體摜破 MA15（close >= ma15），100% 絕對禁止放行任何平多指令！
       - 一票否決任何十字星、微小陰線、影線、碰觸 MA5、中途回調等過敏平倉。
       - 唯一平多放行條件：1m 收盤價實體摜破 MA15（close < ma15）或跌破 KC 中軌（close < kc_middle）。

    4. 空單全程護航閘門（Short Holding Immunity - 未站上 MA15 嚴禁平空）：
       - 只要 1m 收盤價未實體站上 MA15（close <= ma15），100% 絕對禁止放行任何平空指令！
       - 一票否決任何十字星、微小陽線、影線、反彈碰觸 MA5 等中途過敏平倉。
       - 唯一平空放行條件：1m 收盤價實體站上 MA15（close > ma15）或突破 KC 中軌（close > kc_middle）。
    """

    def can_exit(
        self,
        position_side: str,
        bar: Dict[str, Any],
        indicators: Optional[Dict[str, Any]] = None,
        net_profit_pct: float = 0.0,
        is_hard_stop: bool = False
    ) -> Tuple[bool, str]:
        """審查當前持倉是否允許平倉。

        Args:
            position_side: 'LONG' or 'SHORT'
            bar: 包含 'close', 'open', 'high', 'low' 等當根 K 棒數據
            indicators: 可包含 'ma5', 'ma15', 'ma15_slope', 'kc_middle' 等指標
            net_profit_pct: 當前浮動淨利潤率
            is_hard_stop: 是否觸發硬停損
        Returns:
            (can_exit: bool, reason: str)
        """
        # 1. 最高級別硬停損保護 (包含 -1.5% 絕對硬止損)
        if is_hard_stop or net_profit_pct <= -0.015:
            return True, "PASSED: HARD_STOP_TRIGGERED"

        # 2. 達到 10% 淨利後的動態鎖利，放行！
        if net_profit_pct >= 0.10:
            return True, "PASSED: AUTHORIZED_NET_PROFIT_LOCK"

        if position_side not in ('LONG', 'SHORT', 'CLOSE_LONG', 'EXIT_LONG', 'CLOSE_SHORT', 'EXIT_SHORT'):
            return True, "PASSED_TREND_GATE"

        indicators = indicators or {}
        close = float(bar.get('close', 0.0))
        ma15 = float(indicators.get('ma15', bar.get('ma15', 0.0)))
        kc_middle = float(indicators.get('kc_middle', bar.get('kc_middle', 0.0)))

        # 3. 多單全程護航閘門（Long Holding Immunity - 未破 MA15 嚴禁平多）
        if position_side in ('LONG', 'CLOSE_LONG', 'EXIT_LONG'):
            # 唯一平多放行條件：1m 收盤價實體摜破 MA15（close < ma15）或跌破 KC 中軌（close < kc_middle）
            if (ma15 > 0 and close < ma15) or (kc_middle > 0 and close < kc_middle):
                return True, "PASSED_TREND_GATE"

            # 只要收盤在 MA15 之上，一律「一票否決平倉 (VETO)」，徹底封死十字星/反向K/碰線平倉
            if ma15 > 0 and close >= ma15:
                return False, "REJECT_EXIT: TREND_INTACT_ABOVE_MA15 (VETO)"

        # 4. 空單全程護航閘門（Short Holding Immunity - 未站上 MA15 嚴禁平空）
        elif position_side in ('SHORT', 'CLOSE_SHORT', 'EXIT_SHORT'):
            # 唯一平空放行條件：1m 收盤價實體站上 MA15（close > ma15）或突破 KC 中軌（close > kc_middle）
            if (ma15 > 0 and close > ma15) or (kc_middle > 0 and close > kc_middle):
                return True, "PASSED_TREND_GATE"

            # 只要收盤在 MA15 之下，一律「一票否決平倉 (VETO)」，全程死抱暴跌浪
            if ma15 > 0 and close <= ma15:
                return False, "REJECT_EXIT: TREND_INTACT_BELOW_MA15 (VETO)"

        return True, "PASSED_TREND_GATE"
