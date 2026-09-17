"""Unified Entry Strategy evaluating streamlined entry methods.
Implements IEntryStrategy interface.
"""
import math
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_streamlined_entry_signal(df: pd.DataFrame, side: str, live_price: float) -> Tuple[bool, str]:
    """
    極簡中庸進場：過濾忽上忽下，放行乾淨起爆與均線交叉
    """
    try:
        latest = df.iloc[-1]
        atr = float(latest.get("atr", live_price * 0.01))
        ma3 = float(latest["ma3"])
        ma15 = float(latest["ma15"])
        kc_mid = float(latest["kc_middle"])

        # 1. 基礎通道帶寬保護（過濾死水盤）
        if (float(latest["kc_upper"]) - float(latest["kc_lower"])) < (1.0 * atr):
            return False, "BLOCK_BANDWIDTH_TOO_FLAT"

        # 計算 CK 斜率 fallback
        if "kc_middle_slope" in latest:
            kc_middle_slope = float(latest["kc_middle_slope"])
        else:
            kc_middle_slope = float(df.iloc[-1]["kc_middle"]) - float(df.iloc[-2]["kc_middle"])

        # 2. 空單入口 (SHORT)
        if side == "SHORT":
            if kc_middle_slope > 0:
                return False, "BLOCK_SHORT_KC_MIDDLE_STILL_RISING"

            # 觸發條件 A：大黑 K 摜破中軌起爆 (優先豁免空間檢查)
            is_breakdown = (float(latest["open"]) - float(latest["close"]) >= 0.5 * atr) and (float(latest["close"]) < kc_mid)
            if is_breakdown:
                return True, "ALLOW_SHORT_BREAKDOWN_MOMENTUM"

            # 觸發條件 B：MA3 死叉且偏離容許度放寬至 0.35 ATR
            is_dead_cross = (ma3 <= ma15) and (live_price <= ma15 + 0.35 * atr)
            if not is_dead_cross:
                return False, "WAIT_SHORT_TRIGGER"

            # 僅在走觸發 B 時檢查距下軌空間
            if (live_price - float(latest["kc_lower"])) < (0.35 * atr):
                return False, "BLOCK_SHORT_FLOOR_EXHAUSTED"

            return True, "ALLOW_SHORT_ENTRY"

        # 3. 多單入口 (LONG)
        elif side == "LONG":
            if kc_middle_slope < 0:
                return False, "BLOCK_LONG_KC_MIDDLE_STILL_FALLING"

            # 觸發條件 A：大紅 K 突破中軌起爆 (優先豁免空間檢查)
            is_breakout = (float(latest["close"]) - float(latest["open"]) >= 0.5 * atr) and (float(latest["close"]) > kc_mid)
            if is_breakout:
                return True, "ALLOW_LONG_BREAKOUT_MOMENTUM"

            # 觸發條件 B：MA3 金叉且偏離容許度放寬至 0.35 ATR
            is_golden_cross = (ma3 >= ma15) and (live_price >= ma15 - 0.35 * atr)
            if not is_golden_cross:
                return False, "WAIT_LONG_TRIGGER"

            # 僅在走觸發 B 時檢查距上軌空間
            if (float(latest["kc_upper"]) - live_price) < (0.35 * atr):
                return False, "BLOCK_LONG_CEILING_EXHAUSTED"

            return True, "ALLOW_LONG_ENTRY"
            
    except (AttributeError, KeyError, TypeError, ValueError, IndexError) as e:
        return False, "WAIT_INSUFFICIENT_INDICATORS"

    return False, "INVALID_SIDE"


class UnifiedEntryStrategy(IEntryStrategy):
    """標準進場策略類別，確保 evaluate_entry 正確包含於類別內部"""

    def evaluate_entry(
        self,
        frame: pd.DataFrame,
        price: float,
        side: str,
        **kwargs: Any
    ) -> Tuple[bool, str, Dict[str, Any]]:
        
        position = kwargs.get("position")
        if position and position.get("pivot_reversal_exit_pending"):
            return False, "WAIT_PIVOT_REVERSAL_UNLOCK", {"action": "WAIT"}

        if frame is None or len(frame) < 10 or "kc_middle" not in frame.columns:
            return False, "WAIT_INSUFFICIENT_DATA_OR_INDICATORS", {"action": "WAIT"}

        ok, reason = check_streamlined_entry_signal(frame, side, price)
        if ok:
            return True, reason, {"action": "ENTER", "side": side, "reason": reason}
            
        return False, reason, {"action": "WAIT"}
