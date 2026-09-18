"""Unified Entry Strategy evaluating streamlined entry methods.
Implements IEntryStrategy interface.
"""
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str]:
    """
    嚴格起爆與均線進場：確保起爆必須帶有極高動能與正確的方向
    """
    latest = df.iloc[-1]
    atr = latest.get("atr", live_price * 0.01)
    ma3 = latest["ma3"]
    ma15 = latest["ma15"]
    kc_mid = latest["kc_middle"]

    # --- 基礎過濾 ---
    # 0. 峰谷過濾：防止追高殺跌 ( > 1.5 ATR 擋掉, 空間寬容帶 V5.2)
    dist_from_mid = abs(live_price - kc_mid)
    if dist_from_mid > (1.5 * atr):
        velocity_slowdown = kwargs.get("velocity_slowdown", False)
        # 容許延伸至 1.8 ATR (相當於 kc_upper + 0.3 ATR)
        if dist_from_mid <= (1.8 * atr) and velocity_slowdown:
            pass
        else:
            return False, "BLOCK_EXTREME_PEAK_OR_VALLEY"

    # 1. 帶寬保護：過濾死水盤 (V5.2 動態寬度過濾)
    is_bandwidth_ok = (latest["kc_upper"] - latest["kc_lower"]) >= (1.0 * atr)
    atr_expanding = False
    if len(df) > 6:
        recent_atr = df['atr'].iloc[-3:].mean()
        prev_atr = df['atr'].iloc[-6:-3].mean()
        if prev_atr > 0 and (recent_atr / prev_atr - 1) >= 0.20:
            atr_expanding = True
    if not is_bandwidth_ok and not atr_expanding:
        return False, "BLOCK_BANDWIDTH_TOO_FLAT"

    # 2. 嚴格成交量檢查 (門檻 1.3 倍)
    avg_vol = df["volume"].tail(10).mean()
    is_high_volume = latest["volume"] > (avg_vol * 1.3)

    prev = df.iloc[-2] if len(df) >= 2 else latest
    latest_open = float(latest["open"])
    latest_close = float(latest["close"])
    prev_open = float(prev["open"])
    prev_close = float(prev["close"])

    # 3. 動能攔截：K 棒顏色防呆 (Momentum Anti-Bounce)
    if side == "SHORT":
        if (latest_close > latest_open) and ((latest_close - latest_open) >= 0.2 * atr):
            return False, "BLOCK_AGAINST_BOUNCE"
    elif side == "LONG":
        if (latest_open > latest_close) and ((latest_open - latest_close) >= 0.2 * atr):
            return False, "BLOCK_AGAINST_BOUNCE"

    # --- 空單邏輯 (SHORT) ---
    if side == "SHORT":
        # 規則 A：中軌必須是下降趨勢 (Slope < 0)
        kc_slope = float(latest.get("kc_middle_slope", 0))
        if kc_slope >= 0:
            return False, "BLOCK_SHORT_KC_MIDDLE_STILL_RISING"

        # 規則 B：雙根實體破軌規範 (結構性破壞)
        is_breakdown = (
            (latest_open - latest_close >= 0.25 * atr)
            and (prev_open - prev_close >= 0.25 * atr)
            and (latest_close < kc_mid)
            and is_high_volume
        )
        if is_breakdown:
            return True, "ALLOW_SHORT_BREAKDOWN_MOMENTUM_STRICT"

        # 規則 C：MA3 死叉且偏離容許度 0.7 ATR
        is_dead_cross = (ma3 <= ma15) and (live_price <= ma15 + 0.7 * atr)
        if not is_dead_cross:
            return False, "WAIT_SHORT_TRIGGER"

        # 檢查距下軌空間
        if (live_price - float(latest["kc_lower"])) < (0.7 * atr):
            return False, "BLOCK_SHORT_FLOOR_EXHAUSTED"

        return True, "ALLOW_SHORT_ENTRY"

    # --- 多單邏輯 (LONG) ---
    elif side == "LONG":
        # 規則 A：中軌必須是上升趨勢 (Slope > 0)
        kc_slope = float(latest.get("kc_middle_slope", 0))
        if kc_slope <= 0:
            return False, "BLOCK_LONG_KC_MIDDLE_STILL_FALLING"

        # 規則 B：雙根實體破軌規範 (結構性破壞)
        is_breakout = (
            (latest_close - latest_open >= 0.25 * atr)
            and (prev_close - prev_open >= 0.25 * atr)
            and (latest_close > kc_mid)
            and is_high_volume
        )
        if is_breakout:
            return True, "ALLOW_LONG_BREAKOUT_MOMENTUM_STRICT"

        # 規則 C：MA3 金叉且偏離容許度 0.7 ATR
        is_golden_cross = (ma3 >= ma15) and (live_price >= ma15 - 0.7 * atr)
        if not is_golden_cross:
            return False, "WAIT_LONG_TRIGGER"

        # 檢查距上軌空間
        if (float(latest["kc_upper"]) - live_price) < (0.7 * atr):
            return False, "BLOCK_LONG_CEILING_EXHAUSTED"

        return True, "ALLOW_LONG_ENTRY"

    return False, "INVALID_SIDE"


class UnifiedEntryStrategy(IEntryStrategy):
    def evaluate_entry(self, frame, price, side, **kwargs):
        if frame is None or len(frame) < 10 or "kc_middle" not in frame.columns:
            return False, "WAIT_INSUFFICIENT_DATA_OR_INDICATORS", {"action": "WAIT"}

        try:
            ok, reason = check_streamlined_entry_signal(frame, side, price, **kwargs)
        except Exception:
            return False, "WAIT_INSUFFICIENT_INDICATORS", {"action": "WAIT"}

        if ok:
            return True, reason, {"action": "ENTER", "side": side, "reason": reason}
        return False, reason, {"action": "WAIT"}
