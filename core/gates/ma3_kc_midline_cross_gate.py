"""
Gate: MA3 × KC 中軌多空進場授權規則 (MA3KcMidlineCrossGate)

規則定義：
一、多單進場規則 (Long Entry):
    1. 前一根已收線 K 棒的 MA3 小於或等於 KC 中軌：MA3[t-1] <= KC_MID[t-1]
    2. 當前已收線 K 棒的 MA3 嚴格大於 KC 中軌：MA3[t] > KC_MID[t]
    3. 當前 MA3 大於前一根 MA3，即 MA3 斜率向上：MA3[t] > MA3[t-1]
    4. 當前及前一根 K 棒資料均有效、已收線且足夠新鮮。
    5. 必要指標均為有效數值，不得使用 NaN、無限值、過期快照或未確認的即時值。

二、空單進場規則 (Short Entry):
    1. 前一根已收線 K 棒的 MA3 大於或等於 KC 中軌：MA3[t-1] >= KC_MID[t-1]
    2. 當前已收線 K 棒的 MA3 嚴格小於 KC 中軌：MA3[t] < KC_MID[t]
    3. 當前 MA3 小於前一根 MA3，即 MA3 斜率向下：MA3[t] < MA3[t-1]
    4. 當前及前一根 K 棒資料均有效、已收線且足夠新鮮。
    5. 必要指標均為有效數值，不得使用 NaN、無限值、過期快照或未確認的即時值。

三、嚴格禁止：
    - MA3 已在 KC 中軌上方/下方，但沒有新發生穿越者，不得重新授權開倉。
    - 尚未收線之 K 棒不得提前確認交叉。
    - 同一根已收線 K 棒不得重複觸發開倉。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from core.services.candle_data import closed_entry_candles

MAX_STALENESS_MS = 180_000.0


def _is_finite_num(val: Any) -> bool:
    try:
        v = float(val)
        return math.isfinite(v) and v > 0
    except (TypeError, ValueError, OverflowError):
        return False


def _strict_true(val: Any) -> bool:
    return val is True or val == 1 or str(val).strip().lower() in {"true", "1"}


def _check_freshness(frame: Any, closed: pd.DataFrame) -> bool:
    attrs = getattr(frame, "attrs", {})
    if not isinstance(attrs, dict) or "snapshot_ms" not in attrs:
        return True
    try:
        snapshot_ms = float(attrs["snapshot_ms"])
        interval = float(attrs.get("timeframe_ms") or 60_000.0)
        last_stamp = float(closed.iloc[-1].get("timestamp", 0))
        close_time = last_stamp + interval
        age = snapshot_ms - close_time
        return 0.0 <= age <= max(MAX_STALENESS_MS, interval * 2.0)
    except (TypeError, ValueError, OverflowError, IndexError):
        return False


def evaluate_ma3_kc_midline_cross(
    frame: Any,
    side: str,
    symbol: str = "",
    *,
    last_trade_bar_time: Optional[int] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    評估 MA3 與 KC 中軌的交叉進場授權。

    回傳：(passed, reason, details)
    """
    side_upper = str(side or "").upper()
    details: Dict[str, Any] = {
        "symbol": symbol,
        "side": side_upper,
        "rule": "MA3_KC_MIDLINE_CROSS",
    }

    if side_upper not in ("LONG", "SHORT"):
        return False, "INVALID_SIDE", details

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return False, "MISSING_CANDLE_DATA", details

    # 1. 驗證最新 K 棒是否明確收線（禁止使用未收線 K 棒提前確認）
    if "is_closed" in frame.columns:
        if not _strict_true(frame.iloc[-1]["is_closed"]):
            return False, "LATEST_CANDLE_NOT_CONFIRMED_CLOSED", details

    # 2. 取得有效收線 K 棒，至少需 2 根
    closed = closed_entry_candles(frame)
    if closed.empty or len(closed) < 2:
        return False, "INSUFFICIENT_CLOSED_CANDLES", details

    # 3. 新鮮度驗證（不得使用過期快照）
    if not _check_freshness(frame, closed):
        return False, "DATA_STALE_OR_EXPIRED", details

    previous = closed.iloc[-2]
    current = closed.iloc[-1]

    # 4. 提取時間戳與重覆交易檢查
    try:
        bar_time = int(current.get("timestamp", current.get("open_time", 0)))
    except (TypeError, ValueError, OverflowError):
        bar_time = 0

    if bar_time <= 0:
        return False, "INVALID_CANDLE_TIMESTAMP", details

    details["bar_time"] = bar_time

    if last_trade_bar_time is not None:
        try:
            if int(last_trade_bar_time) == bar_time:
                return False, "BAR_ALREADY_TRADED", details
        except (TypeError, ValueError, OverflowError):
            pass

    # 5. 計算 / 提取指標數值
    try:
        closes = pd.to_numeric(closed["close"], errors="coerce")
        if "ma3" in closed.columns:
            ma3_series = pd.to_numeric(closed["ma3"], errors="coerce")
        else:
            ma3_series = closes.rolling(3, min_periods=3).mean()

        ma3_now = float(ma3_series.iloc[-1])
        ma3_prev = float(ma3_series.iloc[-2])

        if "kc_middle" in closed.columns:
            kc_mid_now = float(current["kc_middle"])
            kc_mid_prev = float(previous["kc_middle"])
        elif "kc_upper" in closed.columns and "kc_lower" in closed.columns:
            kc_mid_now = (float(current["kc_upper"]) + float(current["kc_lower"])) / 2.0
            kc_mid_prev = (float(previous["kc_upper"]) + float(previous["kc_lower"])) / 2.0
        else:
            return False, "MISSING_KC_MIDDLE_INDICATOR", details

    except (TypeError, ValueError, OverflowError, IndexError, KeyError):
        return False, "INDICATOR_CALCULATION_ERROR", details

    # 6. 數值有效性檢核 (不得使用 NaN、無限值、<= 0)
    vals = (ma3_now, ma3_prev, kc_mid_now, kc_mid_prev)
    if not all(_is_finite_num(v) for v in vals):
        return False, "INVALID_OR_NON_FINITE_INDICATORS", details

    ma3_slope = ma3_now - ma3_prev
    details.update({
        "ma3_prev": ma3_prev,
        "ma3_now": ma3_now,
        "kc_mid_prev": kc_mid_prev,
        "kc_mid_now": kc_mid_now,
        "ma3_slope": ma3_slope,
    })

    # 7. 交叉與斜率嚴格判定
    if side_upper == "LONG":
        # 多單進場鐵律：
        # 1. 前一根已收線 MA3 <= KC 中軌 (前一根仍在軌下或碰軌)
        # 2. 當前已收線 MA3 > KC 中軌 (當前實質上穿中軌)
        # 3. 當前 MA3 > 前一根 MA3 (MA3 斜率向上)
        if ma3_prev > kc_mid_prev:
            return False, "NO_FRESH_CROSS_MA3_ALREADY_ABOVE_MIDLINE", details
        if ma3_now <= kc_mid_now:
            return False, "MA3_NOT_ABOVE_MIDLINE", details
        if ma3_now <= ma3_prev:
            return False, "MA3_SLOPE_NOT_UPWARD", details

        details["cross_result"] = "LONG_CROSS_CONFIRMED"
        return True, "MA3_KC_MIDLINE_CROSS_CONFIRMED", details

    else:  # SHORT
        # 空單進場鐵律：
        # 1. 前一根已收線 MA3 >= KC 中軌 (前一根仍在軌上或碰軌)
        # 2. 當前已收線 MA3 < KC 中軌 (當前實質下穿中軌)
        # 3. 當前 MA3 < 前一根 MA3 (MA3 斜率向下)
        if ma3_prev < kc_mid_prev:
            return False, "NO_FRESH_CROSS_MA3_ALREADY_BELOW_MIDLINE", details
        if ma3_now >= kc_mid_now:
            return False, "MA3_NOT_BELOW_MIDLINE", details
        if ma3_now >= ma3_prev:
            return False, "MA3_SLOPE_NOT_DOWNWARD", details

        details["cross_result"] = "SHORT_CROSS_CONFIRMED"
        return True, "MA3_KC_MIDLINE_CROSS_CONFIRMED", details
