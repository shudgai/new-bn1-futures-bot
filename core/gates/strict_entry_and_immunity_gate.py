"""StrictEntryAndImmunityGate
整合「3 根平滑斜率防走平開倉」與「MA15 趨勢護航全程死抱否決權」之核心閘門。
"""
from typing import Tuple, Dict, Any, Optional
import pandas as pd


class StrictEntryAndImmunityGate:
    def __init__(self, min_slope_pct: float = 0.0010, cooldown_bars: int = 3):
        self.min_slope_pct = min_slope_pct
        self.cooldown_bars = cooldown_bars
        self.last_exit_bar_index = -999

    def record_exit(self, current_bar_index: int):
        """記錄平倉時的 K 棒索引，用於觸發冷卻防線"""
        self.last_exit_bar_index = current_bar_index

    # =========================================================================
    # 一、 進場前置審查：MA5 走平 / 弧頂 100% 一票否決 (VETO)
    # =========================================================================
    def can_open(self, side: str, df: Any, current_bar_index: int = 0) -> Tuple[bool, str]:
        # 1. 冷卻期檢核 (防連環刷單)
        if current_bar_index - self.last_exit_bar_index <= self.cooldown_bars:
            return False, f"VETO: 平倉冷卻中 (已過 {current_bar_index - self.last_exit_bar_index}/{self.cooldown_bars} 根)，禁止開倉"

        if df is None or len(df) < 5:
            return False, "VETO: K 棒數量不足，拒絕開倉"

        # 必須採用已收盤 K 棒 (iloc[-2] 剛收盤, iloc[-4] 前兩根)
        curr_bar = df.iloc[-2]
        prev_bar_2 = df.iloc[-4]
        price = float(curr_bar['close'])

        ma5_curr = float(curr_bar['ma5'])
        ma5_prev2 = float(prev_bar_2['ma5'])
        ma15_curr = float(curr_bar['ma15'])

        # 3 根平滑斜率計算: (MA5_curr - MA5_prev2) / Close
        ma5_slope = (ma5_curr - ma5_prev2) / (price + 1e-9)

        # 多單審查
        if side in ['LONG', 'OPEN_LONG']:
            if ma5_curr <= ma15_curr:
                return False, "VETO_LONG: MA5 尚未大於 MA15"
            # 斜率未達拉升標準 -> 判定走平或弧頂
            if ma5_slope <= self.min_slope_pct:
                return False, f"VETO_LONG: MA5 走平或傾斜不足 (Slope: {ma5_slope:.6f} <= {self.min_slope_pct})，嚴禁頂部接盤"
            return True, "PASSED_ENTRY_LONG"

        # 空單審查
        elif side in ['SHORT', 'OPEN_SHORT']:
            if ma5_curr >= ma15_curr:
                return False, "VETO_SHORT: MA5 尚未小於 MA15"
            # 斜率未達下殺標準 -> 判定走平或築底
            if ma5_slope >= -self.min_slope_pct:
                return False, f"VETO_SHORT: MA5 走平或傾斜不足 (Slope: {ma5_slope:.6f} >= {-self.min_slope_pct})，嚴禁底部追空"
            return True, "PASSED_ENTRY_SHORT"

        return False, "VETO: UNKNOWN_SIDE"

    # =========================================================================
    # 二、 出場全程護航：未破 MA15 任何常規平倉 100% 一票否決 (VETO)
    # =========================================================================
    def can_exit(
        self,
        side: str,
        closed_bar: Dict[str, Any],
        net_profit_pct: float = 0.0,
        is_hard_stop: bool = False
    ) -> Tuple[bool, str]:
        # 1. 硬停損保護 (最高級別放行: 包含 -1.5% 絕對硬止損)
        if is_hard_stop or net_profit_pct <= -0.015:
            return True, "PASSED: HARD_STOP_TRIGGERED"

        # 2. 淨利鎖利放行 (利潤達 10% 啟動且觸發回吐 20% 防線)
        if net_profit_pct >= 0.10:
            return True, "PASSED: AUTHORIZED_NET_PROFIT_LOCK"

        close = float(closed_bar['close'])
        ma15 = float(closed_bar['ma15'])

        # 3. 多單趨勢全程護航：未跌破 MA15 嚴禁平多 (封死觸軌/影線/十字星平倉)
        if side in ['LONG', 'CLOSE_LONG', 'EXIT_LONG']:
            if close >= ma15:
                return False, "REJECT_EXIT: TREND_INTACT_ABOVE_MA15 (VETO)"
            return True, "PASSED: TREND_BROKEN_BELOW_MA15"

        # 4. 空單趨勢全程護航：未站上 MA15 嚴禁平空 (暴跌全程死抱)
        elif side in ['SHORT', 'CLOSE_SHORT', 'EXIT_SHORT']:
            if close <= ma15:
                return False, "REJECT_EXIT: TREND_INTACT_BELOW_MA15 (VETO)"
            return True, "PASSED: TREND_BROKEN_ABOVE_MA15"

        return True, "PASSED"


# 全域單例
default_strict_gate = StrictEntryAndImmunityGate()
