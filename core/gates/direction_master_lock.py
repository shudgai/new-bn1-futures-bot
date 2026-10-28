"""Direction Master Lock (方向總開關)

Enforces strict directional isolation based on MA3 relative position
and directional slope to KC middle band.

Absolute Rules:
- LONG:
  1. MA3 must be strictly ABOVE KC middle: ma3_curr > kc_middle
  2. MA3 slope must be strictly UPWARD: (ma3_curr > ma5_curr) or (ma3_curr > ma3_prev)
- SHORT:
  1. MA3 must be strictly BELOW KC middle: ma3_curr < kc_middle
  2. MA3 slope must be strictly DOWNWARD: (ma3_curr < ma5_curr) or (ma3_curr < ma3_prev)

Any signal violating these requirements is VETOED with 100% certainty.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional, Tuple, Union

import pandas as pd

logger = logging.getLogger("DirectionMasterLock")


def _to_float(val: Any) -> Optional[float]:
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


def evaluate_direction_master_lock(
    side: str,
    ma3_curr: float,
    ma3_prev: float,
    kc_middle: float,
    ma5_curr: Optional[float] = None,
    is_swing_pivot: bool = False,
    direction_override: bool = False,
    ma15_curr: Optional[float] = None,
    curr_close: Optional[float] = None,
    curr_open: Optional[float] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    方向總開關純數學核驗 (嚴格一票否決硬條件)

    🟢 開多絕對門檻（沒滿足任何一項，強制拒絕開多）：
    1. MA3 當前必須明確向上: ma3_curr > ma3_prev
    2. MA3 必須位於慢線上方 (或金叉狀態): ma3_curr >= ma15_curr (若無 ma15 則比對 kc_middle)
    3. 當前 K 棒必須是實體陽線: curr_close > curr_open (若提供)
    4. 常規開倉 MA3 位於 KC 中軌上方: ma3_curr > kc_middle (轉折特例可豁免中軌位置，但前三項絕不可豁免)

    🔴 開空絕對門檻（沒滿足任何一項，強制拒絕開空）：
    1. MA3 當前必須明確向下: ma3_curr < ma3_prev
    2. MA3 必須位於慢線下方 (或死叉狀態): ma3_curr <= ma15_curr (若無 ma15 則比對 kc_middle)
    3. 當前 K 棒必須是實體陰線: curr_close < curr_open (若提供)
    4. 常規開倉 MA3 位於 KC 中軌下方: ma3_curr < kc_middle (轉折特例可豁免中軌位置，但前三項絕不可豁免)

    Returns:
        (passed: bool, reason: str, details: dict)
    """
    side_upper = str(side or "").upper()
    if side_upper not in ("LONG", "SHORT"):
        return False, "BLOCKED_DIRECTION_LOCK_INVALID_SIDE", {"side": side}

    slow_line = ma15_curr if (ma15_curr is not None and math.isfinite(ma15_curr)) else kc_middle

    details = {
        "side": side_upper,
        "ma3_curr": ma3_curr,
        "ma3_prev": ma3_prev,
        "kc_middle": kc_middle,
        "ma5_curr": ma5_curr,
        "ma15_curr": slow_line,
        "curr_close": curr_close,
        "curr_open": curr_open,
        "is_swing_pivot": is_swing_pivot,
        "direction_override": direction_override,
    }

    if not (math.isfinite(ma3_curr) and math.isfinite(ma3_prev) and math.isfinite(kc_middle)):
        return False, "BLOCKED_DIRECTION_LOCK_INVALID_METRICS", details

    allow_override = is_swing_pivot or direction_override

    if side_upper == "LONG":
        # 1. 中軌位置限制：常規開倉 MA3 必須在中軌上方；轉折特例可豁免
        pos_ok = (ma3_curr > kc_middle) if not allow_override else True
        if not pos_ok:
            return False, "BLOCKED_DIRECTION_LOCK_LONG_BELOW_MID", details

        # 2. MA3 斜率：當前必須明確向上 (絕不可豁免)
        is_ma3_trending_up = ma3_curr > ma3_prev
        if not is_ma3_trending_up:
            return False, "BLOCKED_DIRECTION_LOCK_LONG_SLOPE_DOWN", details

        # 3. MA3 與慢線：特例/轉折/穿中軌豁免慢線限制；常規開倉要求 MA3 高於慢線
        is_ma3_above_slow = True if allow_override else ((ma3_curr >= ma15_curr) if (ma15_curr is not None and math.isfinite(ma15_curr)) else (ma3_curr >= kc_middle))
        if not is_ma3_above_slow:
            return False, "BLOCKED_DIRECTION_LOCK_LONG_MA3_BELOW_SLOW", details

        # 4. K 棒形態：常規開倉當前 K 棒必須是實體陽線 (allow_override 轉折時放寬)
        if not allow_override and curr_close is not None and curr_open is not None and math.isfinite(curr_close) and math.isfinite(curr_open):
            if not (curr_close > curr_open):
                return False, "BLOCKED_DIRECTION_LOCK_LONG_NOT_BULLISH_CANDLE", details

        return True, "DIRECTION_LOCK_PASS", details

    else:  # SHORT
        # 1. 中軌位置限制：常規開倉 MA3 必須在中軌下方；轉折特例可豁免
        pos_ok = (ma3_curr < kc_middle) if not allow_override else True
        if not pos_ok:
            return False, "BLOCKED_DIRECTION_LOCK_SHORT_ABOVE_MID", details

        # 2. MA3 斜率：當前必須明確向下 (絕不可豁免)
        is_ma3_trending_down = ma3_curr < ma3_prev
        if not is_ma3_trending_down:
            return False, "BLOCKED_DIRECTION_LOCK_SHORT_SLOPE_UP", details

        # 3. MA3 與慢線：特例/轉折/穿中軌豁免慢線限制；常規開倉要求 MA3 低於慢線
        is_ma3_below_slow = True if allow_override else ((ma3_curr <= ma15_curr) if (ma15_curr is not None and math.isfinite(ma15_curr)) else (ma3_curr <= kc_middle))
        if not is_ma3_below_slow:
            return False, "BLOCKED_DIRECTION_LOCK_SHORT_MA3_ABOVE_SLOW", details

        # 4. K 棒形態：常規開倉當前 K 棒必須是實體陰線 (allow_override 轉折時放寬)
        if not allow_override and curr_close is not None and curr_open is not None and math.isfinite(curr_close) and math.isfinite(curr_open):
            if not (curr_close < curr_open):
                return False, "BLOCKED_DIRECTION_LOCK_SHORT_NOT_BEARISH_CANDLE", details

        return True, "DIRECTION_LOCK_PASS", details


def extract_direction_metrics_from_frame(frame: Any) -> Optional[Dict[str, float]]:
    """
    從行情 DataFrame 或 Dict 提取 MA3(curr, prev)、KC 中軌、MA5、MA15、K 棒開收盤價。
    支援已收線 K 棒過濾。
    """
    if frame is None:
        return None

    df = None
    if isinstance(frame, pd.DataFrame):
        df = frame
    elif isinstance(frame, dict):
        raw = frame.get("raw_df") or frame.get("candles") or frame.get("df")
        if isinstance(raw, pd.DataFrame):
            df = raw
        elif isinstance(raw, list) and raw and isinstance(raw[0], dict):
            df = pd.DataFrame(raw)
        else:
            # 可能是單根指標 dict
            m3 = _to_float(frame.get("ma3_curr") or frame.get("ma3"))
            m3_p = _to_float(frame.get("ma3_prev") or frame.get("prev_ma3"))
            kc_m = _to_float(frame.get("kc_middle") or frame.get("kc_mid"))
            m5 = _to_float(frame.get("ma5_curr") or frame.get("ma5"))
            m15 = _to_float(frame.get("ma15_curr") or frame.get("ma15"))
            c_c = _to_float(frame.get("close") or frame.get("curr_close"))
            c_o = _to_float(frame.get("open") or frame.get("curr_open"))
            if m3 is not None and m3_p is not None and kc_m is not None:
                return {
                    "ma3_curr": m3, "ma3_prev": m3_p, "kc_middle": kc_m,
                    "ma5_curr": m5, "ma15_curr": m15 or kc_m,
                    "curr_close": c_c, "curr_open": c_o,
                }
    elif isinstance(frame, list) and frame and isinstance(frame[0], dict):
        df = pd.DataFrame(frame)

    if df is None or len(df) < 2:
        return None

    # 提取已收線 K 棒
    from core.services.candle_data import closed_entry_candles
    closed_df = closed_entry_candles(df)
    if len(closed_df) < 2:
        closed_df = df

    if len(closed_df) < 2:
        return None

    # 計算或讀取 MA3, MA5, MA15, KC_MIDDLE
    c_df = closed_df.copy()
    if "ma3" not in c_df.columns:
        c_df["ma3"] = c_df["close"].rolling(3, min_periods=1).mean()
    if "ma5" not in c_df.columns:
        c_df["ma5"] = c_df["close"].rolling(5, min_periods=1).mean()
    if "ma15" not in c_df.columns:
        c_df["ma15"] = c_df["close"].rolling(15, min_periods=1).mean()
    if "kc_middle" not in c_df.columns:
        c_df["kc_middle"] = c_df["close"].ewm(span=20, adjust=False).mean()

    curr_row = c_df.iloc[-1]
    prev_row = c_df.iloc[-2]

    ma3_curr = _to_float(curr_row.get("ma3"))
    ma3_prev = _to_float(prev_row.get("ma3"))
    kc_middle = _to_float(curr_row.get("kc_middle"))
    ma5_curr = _to_float(curr_row.get("ma5"))
    ma15_curr = _to_float(curr_row.get("ma15")) or kc_middle
    curr_close = _to_float(curr_row.get("close"))
    curr_open = _to_float(curr_row.get("open"))

    if ma3_curr is None or ma3_prev is None or kc_middle is None:
        return None

    return {
        "ma3_curr": ma3_curr,
        "ma3_prev": ma3_prev,
        "kc_middle": kc_middle,
        "ma5_curr": ma5_curr,
        "ma15_curr": ma15_curr,
        "curr_close": curr_close,
        "curr_open": curr_open,
    }


def check_direction_lock_from_frame(frame: Any, side: str, allow_override: bool = False) -> Tuple[bool, str, Dict[str, Any]]:
    """
    由行情資料檢查是否通過方向總開關
    """
    metrics = extract_direction_metrics_from_frame(frame)
    if not metrics:
        return False, "BLOCKED_DIRECTION_LOCK_DATA_INVALID", {"frame_available": frame is not None}

    return evaluate_direction_master_lock(
        side=side,
        ma3_curr=metrics["ma3_curr"],
        ma3_prev=metrics["ma3_prev"],
        kc_middle=metrics["kc_middle"],
        ma5_curr=metrics.get("ma5_curr"),
        ma15_curr=metrics.get("ma15_curr"),
        curr_close=metrics.get("curr_close"),
        curr_open=metrics.get("curr_open"),
        direction_override=allow_override,
    )
