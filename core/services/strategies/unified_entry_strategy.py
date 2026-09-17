"""Unified Entry Strategy evaluating streamlined entry methods.
Implements IEntryStrategy interface.
"""
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_streamlined_entry_signal(df, side: str, live_price: float) -> tuple[bool, str]:
    """
    極簡中庸進場：優先放行帶量起爆，放寬均線交叉容許度
    """
    latest = df.iloc[-1]
    atr = latest.get("atr", live_price * 0.01)
    ma3 = latest["ma3"]
    ma15 = latest["ma15"]
    kc_mid = latest["kc_middle"]
    
    # --- 基礎過濾 ---
    # 1. 帶寬保護：過濾橫盤區間
    if (latest["kc_upper"] - latest["kc_lower"]) < (1.0 * atr):
        return False, "BLOCK_BANDWIDTH_TOO_FLAT"
    
    # 2. 成交量權重：確保起爆帶有資金支持 (成交量 > 10 根均值 1.1 倍)
    avg_vol = df["volume"].tail(10).mean()
    is_high_volume = latest["volume"] > (avg_vol * 1.1)

    # --- 空單邏輯 (SHORT) ---
    if side == "SHORT":
        if latest.get("kc_middle_slope", 0) > 0:
            return False, "BLOCK_SHORT_KC_MIDDLE_STILL_RISING"

        # 觸發 A：大黑 K 摜破中軌起爆 (帶量優先豁免)
        is_breakdown = (latest["open"] - latest["close"] >= 0.5 * atr) and \
                        (latest["close"] < kc_mid) and is_high_volume
        if is_breakdown:
            return True, "ALLOW_SHORT_BREAKDOWN_MOMENTUM_HIGH_VOL"

        # 觸發 B：MA3 死叉且偏離容許度放寬至 0.35 ATR
        is_dead_cross = (ma3 <= ma15) and (live_price <= ma15 + 0.35 * atr)
        if not is_dead_cross:
            return False, "WAIT_SHORT_TRIGGER"

        # 檢查空間限制
        if (live_price - latest["kc_lower"]) < (0.35 * atr):
            return False, "BLOCK_SHORT_FLOOR_EXHAUSTED"

        return True, "ALLOW_SHORT_ENTRY"

    # --- 多單邏輯 (LONG) ---
    elif side == "LONG":
        if latest.get("kc_middle_slope", 0) < 0:
            return False, "BLOCK_LONG_KC_MIDDLE_STILL_FALLING"

        # 觸發 A：大紅 K 突破中軌起爆 (帶量優先豁免)
        is_breakout = (latest["close"] - latest["open"] >= 0.5 * atr) and \
                       (latest["close"] > kc_mid) and is_high_volume
        if is_breakout:
            return True, "ALLOW_LONG_BREAKOUT_MOMENTUM_HIGH_VOL"

        # 觸發 B：MA3 金叉且偏離容許度放寬至 0.35 ATR
        is_golden_cross = (ma3 >= ma15) and (live_price >= ma15 - 0.35 * atr)
        if not is_golden_cross:
            return False, "WAIT_LONG_TRIGGER"

        # 檢查空間限制
        if (latest["kc_upper"] - live_price) < (0.35 * atr):
            return False, "BLOCK_LONG_CEILING_EXHAUSTED"

        return True, "ALLOW_LONG_ENTRY"

    return False, "INVALID_SIDE"

class UnifiedEntryStrategy(IEntryStrategy):
    def evaluate_entry(self, frame, price, side, **kwargs):
        if frame is None or len(frame) < 10 or "kc_middle" not in frame.columns:
            return False, "WAIT_INSUFFICIENT_DATA_OR_INDICATORS", {"action": "WAIT"}

        try:
            ok, reason = check_streamlined_entry_signal(frame, side, price)
        except Exception:
            return False, "WAIT_INSUFFICIENT_INDICATORS", {"action": "WAIT"}
            
        if ok:
            return True, reason, {"action": "ENTER", "side": side, "reason": reason}
        return False, reason, {"action": "WAIT"}
