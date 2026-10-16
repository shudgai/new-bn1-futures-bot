"""
MacroCandleStructureGate
提供 AI 全域視角，透過通道相對位置、乖離率與 K 棒形態，
在開倉前進行強制結構性審查。
"""

import math
from typing import Optional

from core.gates.entry_pattern_permitted_gate import EntryPatternPermittedGate


class MacroCandleStructureGate:
    def __init__(self, max_bias_atr: float = 1.2):
        self.max_bias_atr = max_bias_atr

    def can_open(
        self,
        side: str,
        closed_bar: dict,
        indicators: dict,
        *,
        entry_pattern: Optional[str] = None,
        entry_signal_mode: Optional[str] = None,
        entry_mode: Optional[str] = None,
        structure_confirmation: Optional[str] = None,
        structure_confirmed: bool = False,
        channel_expanding: bool = False,
    ) -> tuple[bool, str]:
        """
        AI 開倉前置審查接口
        :param side: 'LONG' 或 'SHORT'
        :param closed_bar: 最近一根已確認收盤的 K 棒字典 (open, high, low, close, volume)
        :param indicators: 指標字典 (kc_upper, kc_lower, kc_middle, ma15, atr)
        :return: (is_allowed: bool, reason: str)
        """
        supplied_pattern = (
            entry_pattern
            if entry_pattern is not None
            else entry_signal_mode
            if entry_signal_mode is not None
            else entry_mode
        )
        pattern_allowed, pattern_reason = (
            EntryPatternPermittedGate.check_pattern(supplied_pattern)
        )
        if not pattern_allowed:
            return False, pattern_reason
        if (
            entry_pattern is not None
            and entry_signal_mode is not None
            and EntryPatternPermittedGate.normalize(entry_pattern)
            != EntryPatternPermittedGate.normalize(entry_signal_mode)
        ):
            return False, "REJECT_UNAUTHORIZED_ENTRY_PATTERN"
        mode = EntryPatternPermittedGate.normalize(
            supplied_pattern
        )

        required_bar = ("open", "high", "low", "close")
        required_indicators = ("kc_upper", "kc_lower", "kc_middle", "ma15", "atr")
        if any(field not in closed_bar for field in required_bar) or any(
            field not in indicators for field in required_indicators
        ):
            return False, "VETO_INVALID_STRUCTURE_DATA"
        try:
            close = float(closed_bar['close'])
            open_price = float(closed_bar['open'])
            high = float(closed_bar['high'])
            low = float(closed_bar['low'])
            kc_upper = float(indicators['kc_upper'])
            kc_lower = float(indicators['kc_lower'])
            kc_middle = float(indicators['kc_middle'])
            ma15 = float(indicators['ma15'])
            atr = float(indicators['atr'])
        except (TypeError, ValueError, OverflowError):
            return False, "VETO_INVALID_STRUCTURE_DATA"

        # 1. 計算通道相對位置百分比 (0.0 = 下軌, 1.0 = 上軌)
        kc_range = kc_upper - kc_lower
        kc_pos = (close - kc_lower) / kc_range if kc_range > 0 else 0.5

        # 2. 計算對 MA15 的乖離幅度 (以 ATR 為單位)
        bias_atr = abs(close - ma15) / (atr if atr > 0 else 1e-8)

        # 3. K 棒影線與實體計算
        body = abs(close - open_price)
        upper_wick = high - max(open_price, close)
        lower_wick = min(open_price, close) - low
        candle_range = high - low
        values = (
            close, open_price, high, low, kc_upper, kc_lower,
            kc_middle, ma15, atr,
        )
        if (
            not all(math.isfinite(value) for value in values)
            or kc_range <= 0
            or atr <= 0
            or candle_range <= 0
            or high < max(open_price, close, low)
            or low > min(open_price, close, high)
        ):
            return False, "VETO_INVALID_STRUCTURE_DATA"

        confirmation = str(structure_confirmation or "").upper()
        if mode == "BREAKOUT" and (
            not channel_expanding or confirmation != "KC_OUTER_BREAKOUT"
        ):
            return False, "VETO_BREAKOUT: KC 通道未擴張或缺少有效破軌確認"
        if mode == "CROSSOVER" and (
            not structure_confirmed
            or confirmation not in {
                "MA5_MA15_1M_CROSS",
                "MA5_MA15_5M_CONVERGENCE",
            }
        ):
            return False, "VETO_CROSSOVER: 缺少已確認的 1M 交叉或 5M 均線收斂"
        if mode == "REVERSAL" and (
            not structure_confirmed
            or confirmation not in {
                "CONFIRMED_KC_OUTER_PEAK_VALLEY",
                "TOP_BOTTOM_DIVERGENCE",
                "PINBAR_EXHAUSTION_TURN",
                "CONFIRMED_KC_OUTER_PIVOT_THEN_MA5_MA15_CROSS",
            }
        ):
            return False, "VETO_REVERSAL: 缺少峰谷背離或長針力竭確認"
        if mode == "CONTINUATION" and confirmation == "PROFIT_LOCK_CONTINUATION":
            if not structure_confirmed:
                return False, "VETO_CONTINUATION: 缺少鎖利後延續確認"
            required_signal_indicators = (
                "ma5_current", "ma5_previous",
                "kc_middle_current", "kc_middle_previous",
            )
            try:
                signal_indicators = {
                    key: float(indicators[key])
                    for key in required_signal_indicators
                }
            except (KeyError, TypeError, ValueError, OverflowError):
                return False, "VETO_CONTINUATION: 延續指標資料無效"
            if not all(math.isfinite(value) for value in signal_indicators.values()):
                return False, "VETO_CONTINUATION: 延續指標資料無效"
            ma5_current = signal_indicators["ma5_current"]
            ma5_previous = signal_indicators["ma5_previous"]
            middle_current = signal_indicators["kc_middle_current"]
            middle_previous = signal_indicators["kc_middle_previous"]
            if side == "LONG" and not (
                close > kc_upper
                and close > ma5_current
                and ma5_current > ma5_previous
                and middle_current >= middle_previous
            ):
                return False, "VETO_CONTINUATION_LONG: KC 外軌、MA5 或中軌方向未確認"
            if side == "SHORT" and not (
                close < kc_lower
                and close < ma5_current
                and ma5_current < ma5_previous
                and middle_current <= middle_previous
            ):
                return False, "VETO_CONTINUATION_SHORT: KC 外軌、MA5 或中軌方向未確認"
        if (
            mode == "REVERSAL"
            and confirmation == "CONFIRMED_KC_OUTER_PIVOT_THEN_MA5_MA15_CROSS"
        ):
            try:
                ma5_current = float(indicators["ma5_current"])
                ma5_previous = float(indicators["ma5_previous"])
            except (KeyError, TypeError, ValueError, OverflowError):
                return False, "VETO_REVERSAL: MA5 交叉資料無效"
            if (
                not math.isfinite(ma5_current)
                or not math.isfinite(ma5_previous)
                or (
                    side == "LONG"
                    and not (ma5_current > ma5_previous and close > ma5_current)
                )
                or (
                    side == "SHORT"
                    and not (ma5_current < ma5_previous and close < ma5_current)
                )
            ):
                return False, "VETO_REVERSAL: MA5 斜率或價格位置逆向"

        # =====================================================================
        # 多單審查（買多）
        # =====================================================================
        if side == 'LONG':
            if mode == "BREAKOUT":
                if close <= kc_upper:
                    return False, "VETO_BREAKOUT_LONG: 收盤未有效突破 KC 上軌"
            elif mode == "CONTINUATION":
                if kc_pos < 0.35:
                    return False, f"VETO_CONTINUATION_LONG: KC_Pos {kc_pos:.2f} 低於回踩區"
            elif mode == "CROSSOVER":
                if not 0.35 <= kc_pos <= 0.65:
                    return False, f"VETO_CROSSOVER_LONG: KC_Pos {kc_pos:.2f} 不在中軌區"
            # 規則 2：不可在超買過度延伸時進場
            if bias_atr > self.max_bias_atr:
                return False, f"VETO_LONG: 乖離過大 (Bias: {bias_atr:.2f}x ATR)，嚴禁追多"

            # 規則 3：頂部遇阻上影線不可做多
            pinbar_reversal = (
                mode == "REVERSAL"
                and confirmation == "PINBAR_EXHAUSTION_TURN"
            )
            if pinbar_reversal and not (
                lower_wick > body * 1.5
                or (candle_range > 0 and lower_wick / candle_range > 0.40)
            ):
                return False, "VETO_REVERSAL_LONG: 谷底長下影線未確認"
            if (
                not pinbar_reversal
                and upper_wick > body
                and close < high
            ) or (
                mode in {"CROSSOVER", "REVERSAL"}
                and not pinbar_reversal
                and candle_range > 0
                and upper_wick / candle_range > 0.40
            ):
                return False, "VETO_LONG: K 棒受阻留長上影線，禁止開多"

            return True, "PASSED_MACRO_LONG_GATE"

        # =====================================================================
        # 空單審查（賣空）
        # =====================================================================
        elif side == 'SHORT':
            if mode == "BREAKOUT":
                if close >= kc_lower:
                    return False, "VETO_BREAKOUT_SHORT: 收盤未有效跌破 KC 下軌"
            elif mode == "CONTINUATION":
                if kc_pos > 0.65:
                    return False, f"VETO_CONTINUATION_SHORT: KC_Pos {kc_pos:.2f} 高於回踩區"
            elif mode == "CROSSOVER":
                if not 0.35 <= kc_pos <= 0.65:
                    return False, f"VETO_CROSSOVER_SHORT: KC_Pos {kc_pos:.2f} 不在中軌區"
            # 規則 2：不可在超跌過度延伸時追空
            if bias_atr > self.max_bias_atr:
                return False, f"VETO_SHORT: 乖離過大 (Bias: {bias_atr:.2f}x ATR)，嚴禁追空"

            pinbar_reversal = (
                mode == "REVERSAL"
                and confirmation == "PINBAR_EXHAUSTION_TURN"
            )
            if pinbar_reversal and not (
                upper_wick > body * 1.5
                or (candle_range > 0 and upper_wick / candle_range > 0.40)
            ):
                return False, "VETO_REVERSAL_SHORT: 頂部長上影線未確認"
            if close < open_price and candle_range > 0:
                wick_ratio = lower_wick / candle_range
                if wick_ratio > 0.40 or (
                    mode in {"CROSSOVER", "REVERSAL"}
                    and lower_wick > body
                ):
                    return False, "VETO_SHORT: 陰線下影線結構力竭，禁止追空"

            return True, "PASSED_MACRO_SHORT_GATE"

        return False, "UNKNOWN_SIDE"
