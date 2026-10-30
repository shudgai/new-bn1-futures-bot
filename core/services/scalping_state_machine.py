"""
core/services/scalping_state_machine.py
=======================================
有限狀態機 (Finite State Machine) 超級精簡核心模組
將原先互相衝突的 spaghetti if-else 判斷，收斂為純粹的狀態機架構：
狀態有且僅有 3 種：
1. "SHORT" (持有空單)
2. "LONG"  (持有多單)
3. "FLAT"  (無持倉觀望)

嚴格執行：
- 狀態 1 (SHORT)：收盤站上 MA15 (V轉快速離場) 或 方案1鎖利 -> 若大週期為牛市直接一鍵翻單 "CLOSE_SHORT_AND_OPEN_LONG"，否則 "CLOSE_SHORT"
- 狀態 2 (LONG)：收盤跌破 MA15 (快速離場) 或 方案1鎖利 -> 若大週期為熊市直接一鍵翻單 "CLOSE_LONG_AND_OPEN_SHORT"，否則 "CLOSE_LONG"
- 狀態 3 (FLAT)：大週期牛市 + MA3>MA15 (多頭排列) + 價格站穩 MA15 -> "OPEN_LONG"
                 大週期熊市 + MA3<MA15 (空頭排列) + 價格壓制 MA15 -> "OPEN_SHORT"
"""
import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger("ScalpingStateMachine")


class ScalpingStateMachine:
    """極簡有限狀態機交易策略裁決引擎"""

    @staticmethod
    def evaluate(
        position_state: str,
        macro_trend: str,
        current_price: float,
        ma3: float,
        ma15: float,
        entry_price: float = 0.0,
        highest_price: float = 0.0,
        lowest_price: float = 0.0,
        peak_roe_pct: float = 0.0,
    ) -> Tuple[str, str]:
        """
        每根 K 棒收盤 (Bar Close) 或報價更新時執行的核心裁決
        :return: (action, reason)
        action 可選:
          - "HOLD"
          - "CLOSE_SHORT"
          - "CLOSE_LONG"
          - "CLOSE_SHORT_AND_OPEN_LONG"
          - "CLOSE_LONG_AND_OPEN_SHORT"
          - "OPEN_LONG"
          - "OPEN_SHORT"
          - "WAIT"
        """
        pos = str(position_state or "FLAT").upper()
        if pos not in ("SHORT", "LONG"):
            pos = "FLAT"

        macro = str(macro_trend or "NEUTRAL").upper()
        curr_p = float(current_price or 0.0)
        m3 = float(ma3 or 0.0)
        m15 = float(ma15 or 0.0)
        entry_p = float(entry_price or 0.0)
        high_p = float(highest_price or entry_p or curr_p)
        low_p = float(lowest_price or entry_p or curr_p)
        roe_pct = float(peak_roe_pct or 0.0)

        # =========================================================
        # 狀態 1：目前持有空單 (SHORT) 時的離場/翻單判斷
        # =========================================================
        if pos == "SHORT":
            # A. 快速離場條件：收盤強勢站上 MA15 (V轉快速離場，不拖泥帶水)
            is_fast_exit = bool(curr_p > m15)

            # B. 方案 1 鎖利條件：浮盈 >= 10% 且 自最低谷底回吐 >= 20% 且 MA3 > MA15 (金叉確認)
            is_take_profit = False
            if entry_p > 0 and low_p > 0:
                max_gain = (entry_p - low_p) / entry_p
                cur_gain = (entry_p - curr_p) / entry_p
                drawdown = (max_gain - cur_gain) / max_gain if max_gain > 0 else 0.0
                if (max_gain >= 0.10 or roe_pct >= 10.0) and drawdown >= 0.20 and (m3 > m15):
                    is_take_profit = True

            if is_fast_exit or is_take_profit:
                exit_why = "收盤強勢站上 MA15 (V 轉快速離場)" if is_fast_exit else "命中方案 1 鎖利門檻 (回吐>=20% 且 MA3>MA15)"
                if macro == "BULLISH":
                    # 牛市背景：一鍵翻單 (平空並立即開多，絕不留空倉觀望)
                    return "CLOSE_SHORT_AND_OPEN_LONG", f"{exit_why}；當前為大週期牛市，一鍵翻單開多！"
                else:
                    # 熊市/中性：純粹平空落袋
                    return "CLOSE_SHORT", f"{exit_why}；平空落袋觀望。"

            return "HOLD", "空單續抱中 (未突破 MA15 且 MA3 未金叉反轉)"

        # =========================================================
        # 狀態 2：目前持有多單 (LONG) 時的離場/翻單判斷
        # =========================================================
        elif pos == "LONG":
            # A. 快速離場條件：收盤跌破 MA15
            is_fast_exit = bool(curr_p < m15)

            # B. 方案 1 鎖利條件：浮盈 >= 10% 且 自最高點回吐 >= 20% 且 MA3 < MA15 (死叉確認)
            is_take_profit = False
            if entry_p > 0 and high_p > 0:
                max_gain = (high_p - entry_p) / entry_p
                cur_gain = (curr_p - entry_p) / entry_p
                drawdown = (max_gain - cur_gain) / max_gain if max_gain > 0 else 0.0
                if (max_gain >= 0.10 or roe_pct >= 10.0) and drawdown >= 0.20 and (m3 < m15):
                    is_take_profit = True

            if is_fast_exit or is_take_profit:
                exit_why = "收盤跌破 MA15 (快速平多離場)" if is_fast_exit else "命中方案 1 鎖利門檻 (回吐>=20% 且 MA3<MA15)"
                if macro == "BEARISH":
                    # 熊市背景：一鍵翻單 (平多並立即開空)
                    return "CLOSE_LONG_AND_OPEN_SHORT", f"{exit_why}；當前為大週期熊市，一鍵翻單開空！"
                else:
                    # 牛市/中性：純粹平多落袋
                    return "CLOSE_LONG", f"{exit_why}；平多落袋觀望。"

            return "HOLD", "多單續抱中 (守穩 MA15 且 MA3 未死叉跌破)"

        # =========================================================
        # 狀態 3：目前【無持倉 (FLAT)】時的進場/狀態維持補單判斷
        # =========================================================
        else:
            # ⚡ 2026-10-30 核心補充：牛市提早開倉機制 (解決半山腰追多)
            # 1. 取消「強制等待 MA3/MA15 金叉」限制：只要 K 棒收盤價突破 MA15 (curr_p > m15)，無需等待金叉，立刻開多！
            # 2. KC 下軌 V 轉提前試單：若價格觸及下軌後強勢反彈站回 MA3 且 MA3 斜率向上，提前卡位開多！
            if macro == "BULLISH":
                # 條件 A：強勢突破 MA15 (提前起漲點進場) 或 維持多頭排列 (m3 > m15 且 curr_p >= m15)
                if curr_p > m15:
                    return "OPEN_LONG", "大週期牛市背景下，收盤價強勢突破 MA15，無需等待 MA3 金叉，立即提早開多搶佔起漲點！"
                # 條件 B：KC 下軌 V 轉提前試單 (觸及下軌反彈站回 MA3 且 MA3 上拐)
                elif low_p <= m15 and curr_p > m3 and m3 >= m15 * 0.98:
                    return "OPEN_LONG", "大週期牛市背景下，觸及低位後 V 轉站回 MA3，提前試單卡位開多！"

            # 3. 熊市背景：跌破 MA15 或 空頭排列順勢開空
            elif macro == "BEARISH":
                if curr_p < m15:
                    return "OPEN_SHORT", "大週期熊市背景下，收盤價壓制在 MA15 下方，順勢提早開空！"

            return "WAIT", "無持倉觀望中 (等待順勢突破或均線排列成立)"

