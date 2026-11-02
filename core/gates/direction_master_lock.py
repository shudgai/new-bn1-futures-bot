"""Direction Master Lock (方向總開關)

Enforces strict directional isolation based on MA5 relative position
and directional slope to KC middle band.

Absolute Rules:
- LONG:
  1. MA5 must be strictly ABOVE KC middle: ma5_curr > kc_middle
  2. MA5 slope must be strictly UPWARD: (ma5_curr > ma5_curr) or (ma5_curr > ma5_prev)
- SHORT:
  1. MA5 must be strictly BELOW KC middle: ma5_curr < kc_middle
  2. MA5 slope must be strictly DOWNWARD: (ma5_curr < ma5_curr) or (ma5_curr < ma5_prev)

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
    ma5_curr: float,
    ma5_prev: float,
    kc_middle: float,
    is_swing_pivot: bool = False,
    direction_override: bool = False,
    ma15_curr: Optional[float] = None,
    curr_close: Optional[float] = None,
    curr_open: Optional[float] = None,
    kc_middle_prev: Optional[float] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    方向總開關純數學核驗 (嚴格一票否決硬條件)

    🟢 開多絕對門檻（沒滿足任何一項，強制拒絕開多）：
    1. MA5 當前必須明確向上: ma5_curr > ma5_prev
    2. MA5 必須位於慢線上方 (或金叉狀態): ma5_curr >= ma15_curr (若無 ma15 則比對 kc_middle)
    3. 當前 K 棒必須是實體陽線: curr_close > curr_open (若提供)
    4. 常規開倉 MA5 位於 KC 中軌上方: ma5_curr > kc_middle (轉折特例可豁免中軌位置，但前三項絕不可豁免)

    🔴 開空絕對門檻（沒滿足任何一項，強制拒絕開空）：
    1. MA5 當前必須明確向下: ma5_curr < ma5_prev
    2. MA5 必須位於慢線下方 (或死叉狀態): ma5_curr <= ma15_curr (若無 ma15 則比對 kc_middle)
    3. 當前 K 棒必須是實體陰線: curr_close < curr_open (若提供)
    4. 常規開倉 MA5 位於 KC 中軌下方: ma5_curr < kc_middle (轉折特例可豁免中軌位置，但前三項絕不可豁免)

    Returns:
        (passed: bool, reason: str, details: dict)
    """
    side_upper = str(side or "").upper()
    if side_upper not in ("LONG", "SHORT"):
        return False, "BLOCKED_DIRECTION_LOCK_INVALID_SIDE", {"side": side}

    slow_line = ma15_curr if (ma15_curr is not None and math.isfinite(ma15_curr)) else kc_middle

    details = {
        "side": side_upper,
        "ma5_curr": ma5_curr,
        "ma5_prev": ma5_prev,
        "kc_middle": kc_middle,
        "kc_middle_prev": kc_middle_prev,
        "ma15_curr": slow_line,
        "curr_close": curr_close,
        "curr_open": curr_open,
        "is_swing_pivot": is_swing_pivot,
        "direction_override": direction_override,
    }

    if not all(
        math.isfinite(value) and value > 0 for value in (ma5_curr, ma5_prev, kc_middle)
    ) or (
        ma15_curr is not None
        and (not math.isfinite(ma15_curr) or ma15_curr <= 0)
    ):
        return False, "BLOCKED_DIRECTION_LOCK_INVALID_METRICS", details

    allow_override = is_swing_pivot or direction_override

    if side_upper == "LONG":
        # 0. KC 通道中軌斜率硬性一票否決：中軌向下或走平 (未明確向上) 100% 嚴禁開多！
        if kc_middle_prev is not None and math.isfinite(kc_middle_prev):
            if kc_middle <= kc_middle_prev:
                return False, "BLOCKED_DIRECTION_LOCK_LONG_KC_SLOPE_NOT_UP", details

        # 1. 中軌位置限制：常規開倉 MA5 必須在中軌上方；轉折特例可豁免
        pos_ok = (ma5_curr > kc_middle) if not allow_override else True
        if not pos_ok:
            return False, "BLOCKED_DIRECTION_LOCK_LONG_BELOW_MID", details

        # 2. MA5 斜率：當前必須明確向上 (絕不可豁免，向上只能開多)
        is_ma5_trending_up = ma5_curr > ma5_prev
        if not is_ma5_trending_up:
            return False, "BLOCKED_DIRECTION_LOCK_LONG_SLOPE_DOWN", details

        # 3. MA5 與慢線：做多 MA5 必須嚴格位於 MA15 上方 (絕不可豁免，ma5沒在ma15上方不可以開多)
        is_ma5_above_slow = ((ma5_curr > ma15_curr) if (ma15_curr is not None and math.isfinite(ma15_curr)) else (ma5_curr > kc_middle))
        if not is_ma5_above_slow:
            return False, "BLOCKED_DIRECTION_LOCK_LONG_MA5_BELOW_SLOW", details

        # 4. K 棒形態：開多當前 K 棒必須是實體陽線/綠K (curr_close > curr_open，絕不可豁免，向上綠K才能開多)
        if curr_close is not None and curr_open is not None and math.isfinite(curr_close) and math.isfinite(curr_open):
            if not (curr_close > curr_open):
                return False, "BLOCKED_DIRECTION_LOCK_LONG_NOT_BULLISH_CANDLE", details

        return True, "DIRECTION_LOCK_PASS", details

    else:  # SHORT
        # 0. KC 通道中軌斜率硬性一票否決：中軌向上或走平 (未明確向下) 100% 嚴禁開空！
        if kc_middle_prev is not None and math.isfinite(kc_middle_prev):
            if kc_middle >= kc_middle_prev:
                return False, "BLOCKED_DIRECTION_LOCK_SHORT_KC_SLOPE_NOT_DOWN", details

        # 1. 中軌位置限制：常規開倉 MA5 必須在中軌下方；轉折特例可豁免
        pos_ok = (ma5_curr < kc_middle) if not allow_override else True
        if not pos_ok:
            return False, "BLOCKED_DIRECTION_LOCK_SHORT_ABOVE_MID", details

        # 2. MA5 斜率：當前必須明確向下 (絕不可豁免，向下只能開空)
        is_ma5_trending_down = ma5_curr < ma5_prev
        if not is_ma5_trending_down:
            return False, "BLOCKED_DIRECTION_LOCK_SHORT_SLOPE_UP", details

        # 3. MA5 與慢線：做空 MA5 必須嚴格位於 MA15 下方 (絕不可豁免，ma5沒在ma15下方不要開空)
        is_ma5_below_slow = ((ma5_curr < ma15_curr) if (ma15_curr is not None and math.isfinite(ma15_curr)) else (ma5_curr < kc_middle))
        if not is_ma5_below_slow:
            return False, "BLOCKED_DIRECTION_LOCK_SHORT_MA5_ABOVE_SLOW", details

        # 4. K 棒形態：開空當前 K 棒必須是實體陰線/紅K (curr_close < curr_open，絕不可豁免，向下紅K才能開空)
        if curr_close is not None and curr_open is not None and math.isfinite(curr_close) and math.isfinite(curr_open):
            if not (curr_close < curr_open):
                return False, "BLOCKED_DIRECTION_LOCK_SHORT_NOT_BEARISH_CANDLE", details

        return True, "DIRECTION_LOCK_PASS", details


def extract_direction_metrics_from_frame(frame: Any) -> Optional[Dict[str, float]]:
    """
    從行情 DataFrame 或 Dict 提取 MA5(curr, prev)、KC 中軌、MA5、MA15、K 棒開收盤價。
    支援已收線 K 棒過濾。
    """
    if frame is None:
        return None

    df = None
    if isinstance(frame, pd.DataFrame):
        df = frame
    elif isinstance(frame, dict):
        raw = next(
            (frame[key] for key in ("raw_df", "candles", "df") if frame.get(key) is not None),
            None,
        )
        if isinstance(raw, pd.DataFrame):
            df = raw
        elif isinstance(raw, list) and raw and isinstance(raw[0], dict):
            df = pd.DataFrame(raw)
        else:
            # 可能是單根指標 dict
            m5 = _to_float(frame.get("ma5_curr") or frame.get("ma5"))
            m5_p = _to_float(frame.get("ma5_prev") or frame.get("prev_ma5"))
            kc_m = _to_float(frame.get("kc_middle") or frame.get("kc_mid"))
            m15 = _to_float(frame.get("ma15_curr") or frame.get("ma15"))
            c_c = _to_float(frame.get("close") or frame.get("curr_close"))
            c_o = _to_float(frame.get("open") or frame.get("curr_open"))
            if m5 is not None and m5_p is not None and kc_m is not None:
                return {
                    "ma5_curr": m5, "ma5_prev": m5_p, "kc_middle": kc_m,
                    "ma15_curr": m15 or kc_m,
                    "curr_close": c_c, "curr_open": c_o,
                }
    elif isinstance(frame, list) and frame and isinstance(frame[0], dict):
        df = pd.DataFrame(frame)
    elif hasattr(frame, "raw_df") and isinstance(getattr(frame, "raw_df"), pd.DataFrame):
        df = getattr(frame, "raw_df")

    if df is None or len(df) < 2:
        return None

    # 提取已收線 K 棒；若傳入之最後一根為盤中正在形成的 K 棒且 allow_intrabar，則使用該最新棒計算即時 MA5
    from core.services.candle_data import closed_entry_candles
    use_live_tail = bool(
        isinstance(df, pd.DataFrame)
        and len(df) >= 2
        and (
            df.attrs.get("intrabar_authorized")
            or df.attrs.get("direction_override")
            or df.attrs.get("is_agile_trigger")
        )
    )
    if use_live_tail:
        closed_df = df
    else:
        closed_df = closed_entry_candles(df)
        if len(closed_df) < 2:
            closed_df = df

    if len(closed_df) < 2:
        return None

    # 計算或讀取 MA5、MA15 與 KC 中軌。
    c_df = closed_df.copy()
    if "ma5" not in c_df.columns:
        c_df["ma5"] = c_df["close"].rolling(5, min_periods=5).mean()
    if "ma15" not in c_df.columns:
        c_df["ma15"] = c_df["close"].rolling(15, min_periods=1).mean()
    if "kc_middle" not in c_df.columns:
        c_df["kc_middle"] = c_df["close"].ewm(span=20, adjust=False).mean()

    curr_row = c_df.iloc[-1]
    prev_row = c_df.iloc[-2]

    ma5_curr = _to_float(curr_row.get("ma5"))
    ma5_prev = _to_float(prev_row.get("ma5"))
    kc_middle = _to_float(curr_row.get("kc_middle"))
    kc_middle_prev = _to_float(prev_row.get("kc_middle"))
    ma15_curr = _to_float(curr_row.get("ma15")) or kc_middle
    curr_close = _to_float(curr_row.get("close"))
    curr_open = _to_float(curr_row.get("open"))

    if ma5_curr is None or ma5_prev is None or kc_middle is None:
        return None

    return {
        "ma5_curr": ma5_curr,
        "ma5_prev": ma5_prev,
        "kc_middle": kc_middle,
        "kc_middle_prev": kc_middle_prev,
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
        ma5_curr=metrics["ma5_curr"],
        ma5_prev=metrics["ma5_prev"],
        kc_middle=metrics["kc_middle"],
        ma15_curr=metrics.get("ma15_curr"),
        curr_close=metrics.get("curr_close"),
        curr_open=metrics.get("curr_open"),
        kc_middle_prev=metrics.get("kc_middle_prev"),
        direction_override=allow_override,
    )
