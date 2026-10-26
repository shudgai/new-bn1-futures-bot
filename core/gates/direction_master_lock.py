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
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    方向總開關純數學核驗
    
    Returns:
        (passed: bool, reason: str, details: dict)
    """
    side_upper = str(side or "").upper()
    if side_upper not in ("LONG", "SHORT"):
        return False, "BLOCKED_DIRECTION_LOCK_INVALID_SIDE", {"side": side}

    details = {
        "side": side_upper,
        "ma3_curr": ma3_curr,
        "ma3_prev": ma3_prev,
        "kc_middle": kc_middle,
        "ma5_curr": ma5_curr,
    }

    if not (math.isfinite(ma3_curr) and math.isfinite(ma3_prev) and math.isfinite(kc_middle)):
        return False, "BLOCKED_DIRECTION_LOCK_INVALID_METRICS", details

    if side_upper == "LONG":
        # 1. 位置限制：MA3 必須位於 KC 中軌上方
        pos_ok = ma3_curr > kc_middle
        # 2. 斜率限制：MA3 當前方向必須向上
        slope_ok = (ma3_curr > ma3_prev) or (ma5_curr is not None and ma3_curr > ma5_curr)
        details["pos_ok"] = pos_ok
        details["slope_ok"] = slope_ok
        if not pos_ok:
            return False, "BLOCKED_DIRECTION_LOCK_LONG_BELOW_MID", details
        if not slope_ok:
            return False, "BLOCKED_DIRECTION_LOCK_LONG_SLOPE_DOWN", details
        return True, "DIRECTION_LOCK_PASS", details

    else:  # SHORT
        # 1. 位置限制：MA3 必須位於 KC 中軌下方
        pos_ok = ma3_curr < kc_middle
        # 2. 斜率限制：MA3 當前方向必須向下
        slope_ok = (ma3_curr < ma3_prev) or (ma5_curr is not None and ma3_curr < ma5_curr)
        details["pos_ok"] = pos_ok
        details["slope_ok"] = slope_ok
        if not pos_ok:
            return False, "BLOCKED_DIRECTION_LOCK_SHORT_ABOVE_MID", details
        if not slope_ok:
            return False, "BLOCKED_DIRECTION_LOCK_SHORT_SLOPE_UP", details
        return True, "DIRECTION_LOCK_PASS", details


def extract_direction_metrics_from_frame(frame: Any) -> Optional[Dict[str, float]]:
    """
    從行情 DataFrame 或 Dict 提取 MA3(curr, prev)、KC 中軌、MA5。
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
            if m3 is not None and m3_p is not None and kc_m is not None:
                return {"ma3_curr": m3, "ma3_prev": m3_p, "kc_middle": kc_m, "ma5_curr": m5}
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

    # 計算或讀取 MA3, MA5, KC_MIDDLE
    c_df = closed_df.copy()
    if "ma3" not in c_df.columns:
        c_df["ma3"] = c_df["close"].rolling(3, min_periods=1).mean()
    if "ma5" not in c_df.columns:
        c_df["ma5"] = c_df["close"].rolling(5, min_periods=1).mean()
    if "kc_middle" not in c_df.columns:
        c_df["kc_middle"] = c_df["close"].ewm(span=20, adjust=False).mean()

    curr_row = c_df.iloc[-1]
    prev_row = c_df.iloc[-2]

    ma3_curr = _to_float(curr_row.get("ma3"))
    ma3_prev = _to_float(prev_row.get("ma3"))
    kc_middle = _to_float(curr_row.get("kc_middle"))
    ma5_curr = _to_float(curr_row.get("ma5"))

    if ma3_curr is None or ma3_prev is None or kc_middle is None:
        return None

    return {
        "ma3_curr": ma3_curr,
        "ma3_prev": ma3_prev,
        "kc_middle": kc_middle,
        "ma5_curr": ma5_curr,
    }


def check_direction_lock_from_frame(frame: Any, side: str) -> Tuple[bool, str, Dict[str, Any]]:
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
    )
