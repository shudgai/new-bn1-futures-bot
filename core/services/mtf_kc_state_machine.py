"""Multi-timeframe KC entry and position-protection rules."""

from __future__ import annotations

import math
import logging
import time
from collections.abc import Mapping
from typing import Any, Optional

import numpy as np
import pandas as pd

SLOPE_THRESHOLD = 0.005
MAX_ENTRY_BAND_RATIO = 1.0
MTF_KC_STATE_MACHINE = "MTF_KC_STATE_MACHINE"
MTF_KC_EXIT_REASON_PREFIX = "MTF_KC_STATE_MACHINE_"
MTF_CYCLE_EXIT_REASONS = frozenset({
    "MTF_KC_STATE_MACHINE_UPTREND_REVERSAL_EXIT",
    "MTF_KC_STATE_MACHINE_DOWNTREND_REVERSAL_EXIT",
})


def _is_mtf_emergency_exit(reason: Any) -> bool:
    normalized = str(reason or "").upper()
    return any(token in normalized for token in (
        "ACCOUNT_HARD_STOP", "ACCOUNT_CIRCUIT_BREAKER", "CIRCUIT_BREAKER",
        "HARD_STOP_LOSS", "DATA_OUTAGE_HARD_STOP", "LIQUIDATION",
        "WATERFALL", "CATASTROPHIC_DUMP", "CATASTROPHIC_PUMP",
        "熔斷", "瀑布", "帳戶硬止損",
    ))


def _is_mtf_cycle_exit(reason: Any) -> bool:
    return str(reason or "").upper() in MTF_CYCLE_EXIT_REASONS


def mtf_exit_reason_allowed(reason: Any) -> bool:
    return _is_mtf_cycle_exit(reason) or _is_mtf_emergency_exit(reason)


def _number(row: Mapping[str, Any], key: str) -> Optional[float]:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _snapshot_is_fresh(frame: pd.DataFrame, interval_ms: float) -> bool:
    attrs = getattr(frame, "attrs", {})
    try:
        snapshot_ms = float(attrs["snapshot_ms"])
        if snapshot_ms < 1e11:
            snapshot_ms *= 1000.0
        timeframe_ms = float(attrs["timeframe_ms"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if (
        not math.isfinite(snapshot_ms)
        or not math.isclose(timeframe_ms, interval_ms, rel_tol=0, abs_tol=0)
        or snapshot_ms <= 0
    ):
        return False
    now_ms = time.time() * 1000.0
    return 0 <= now_ms - snapshot_ms <= 180_000


def _valid_live_frame(frame: Any, interval_ms: float) -> tuple[pd.DataFrame, pd.Series] | None:
    from core.services.candle_data import closed_entry_candles
    from core.services.low_latency_exit import unwrap_frame_ref

    if not isinstance(frame, pd.DataFrame):
        frame = unwrap_frame_ref(frame)
    if not isinstance(frame, pd.DataFrame) or len(frame) < 2:
        return None
    attrs = getattr(frame, "attrs", {})
    if not _snapshot_is_fresh(frame, interval_ms):
        return None
    live = frame.iloc[-1]
    previous = frame.iloc[-2]
    live_closed = live.get("is_closed")
    previous_closed = previous.get("is_closed")
    if (
        not isinstance(live_closed, (bool, np.bool_)) or bool(live_closed)
        or not isinstance(previous_closed, (bool, np.bool_)) or not bool(previous_closed)
    ):
        return None
    if not math.isclose(float(attrs.get("timeframe_ms", 0)), interval_ms, rel_tol=0, abs_tol=0):
        return None
    closed = closed_entry_candles(frame)
    if closed.empty:
        return None
    try:
        live_stamp = float(live["timestamp"])
        closed_stamp = float(closed.iloc[-1]["timestamp"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) and value > 0 for value in (live_stamp, closed_stamp)):
        return None
    allowed_gaps = (interval_ms, interval_ms / 1000.0)
    if not any(math.isclose(live_stamp - closed_stamp, gap, rel_tol=0, abs_tol=1) for gap in allowed_gaps):
        return None
    return closed, live


def _timestamp_ms(value: Any) -> Optional[float]:
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(timestamp) or timestamp <= 0:
        return None
    return timestamp if timestamp >= 1e11 else timestamp * 1000.0


def _closed_kc_direction(closed: pd.DataFrame) -> str:
    if not isinstance(closed, pd.DataFrame) or len(closed) < 3:
        return "FLAT"
    values = [_number(row, "kc_middle") for _, row in closed.iloc[-3:].iterrows()]
    if any(value is None or value <= 0 for value in values):
        return "FLAT"
    first, second, third = values
    atr = _number(closed.iloc[-1], "atr") or 0.0
    slope_atr = abs(third - first) / (2.0 * atr) if atr > 0 else 0.0
    slope_pct = abs(third - first) / first if first > 0 else 0.0
    if slope_atr < 0.05 and slope_pct < 0.0003:
        return "FLAT"
    if first < second < third:
        return "UPTREND"
    if first > second > third:
        return "DOWNTREND"
    return "FLAT"


def evaluate_ltf_kc_midline_direction(frame_1m: Any) -> dict[str, Any]:
    """Return the confirmed 1m KC midline direction from fresh closed candles."""
    data = _valid_live_frame(frame_1m, 60_000.0)
    if data is None:
        return {
            "valid": False,
            "direction": "UNKNOWN",
            "reason": "BLOCKED_LTF_KC_DIRECTION_DATA_INVALID",
        }
    closed, _ = data
    return {
        "valid": True,
        "direction": _closed_kc_direction(closed),
    }


def _ltf_kc_wave_trend_conflict(closed: pd.DataFrame, side: str) -> Optional[str]:
    """Block entries that oppose the 1m KC middle wave trend (8-12 bar structure)."""
    if (
        not isinstance(closed, pd.DataFrame)
        or len(closed) < 10
        or not {"kc_upper", "kc_lower", "kc_middle"}.issubset(closed.columns)
    ):
        return None
    curr_mid = _number(closed.iloc[-1], "kc_middle")
    prev_mid = _number(closed.iloc[-2], "kc_middle")
    prev2_mid = _number(closed.iloc[-3], "kc_middle")
    mid_6_ago = _number(closed.iloc[-6], "kc_middle")
    mid_10_ago = _number(closed.iloc[-10], "kc_middle")
    curr_close = _number(closed.iloc[-1], "close")
    if None in (curr_mid, prev_mid, prev2_mid, mid_6_ago, mid_10_ago, curr_close):
        return None
    curr_ma5 = _number(closed.iloc[-1], "ma5") if "ma5" in closed.columns else None
    curr_ma15 = _number(closed.iloc[-1], "ma15") if "ma15" in closed.columns else None
    ma15_8_ago = _number(closed.iloc[-8], "ma15") if "ma15" in closed.columns and len(closed) >= 8 else None
    curr_low_kc = _number(closed.iloc[-1], "kc_lower")
    low_kc_8_ago = _number(closed.iloc[-8], "kc_lower") if len(closed) >= 8 else None
    curr_up_kc = _number(closed.iloc[-1], "kc_upper")
    up_kc_8_ago = _number(closed.iloc[-8], "kc_upper") if len(closed) >= 8 else None

    if side == "SHORT":
        confirmed_1m_reversal_down = (
            curr_mid < prev_mid < prev2_mid
            and curr_close < curr_mid
            and curr_ma5 is not None
            and curr_ma15 is not None
            and curr_ma5 < curr_ma15
            and curr_close < curr_ma15
        )
        if confirmed_1m_reversal_down:
            return None
        # 若 1m KC 中軌較 10 根前上升，且 MA15 或 KC 下軌仍處於上升趨勢（代表只是多頭上升通道內的回踩），嚴禁開空！
        wave_mid_up = curr_mid > mid_10_ago and (
            curr_mid > mid_6_ago
            or (curr_ma15 is not None and ma15_8_ago is not None and curr_ma15 >= ma15_8_ago)
            or (curr_low_kc is not None and low_kc_8_ago is not None and curr_low_kc > low_kc_8_ago)
        )
        if wave_mid_up:
            return "BLOCKED_SHORT_ON_UPWARD_LTF_KC_WAVE_TREND"
    elif side == "LONG":
        confirmed_1m_reversal_up = (
            curr_mid > prev_mid > prev2_mid
            and curr_close > curr_mid
            and curr_ma5 is not None
            and curr_ma15 is not None
            and curr_ma5 > curr_ma15
            and curr_close > curr_ma15
        )
        if confirmed_1m_reversal_up:
            return None
        # 若 1m KC 中軌較 10 根前下降，且 MA15 與 KC 上軌仍處於下降趨勢（代表只是空頭下降通道內的反彈），嚴禁開多！
        wave_mid_down = curr_mid < mid_10_ago and (
            curr_mid < mid_6_ago
            or (curr_ma15 is not None and ma15_8_ago is not None and curr_ma15 <= ma15_8_ago)
            or (curr_up_kc is not None and up_kc_8_ago is not None and curr_up_kc < up_kc_8_ago)
        )
        if wave_mid_down:
            return "BLOCKED_LONG_ON_DOWNWARD_LTF_KC_WAVE_TREND"
    return None


def _detect_ltf_outer_band_turn_state(closed: pd.DataFrame) -> Optional[str]:
    """Detect a confirmed 1m KC + MA5/MA15 trend state on full-indicator 1m frames."""
    if (
        not isinstance(closed, pd.DataFrame)
        or len(closed) < 3
        or not {"low", "high", "close", "kc_lower", "kc_upper", "kc_middle"}.issubset(
            closed.columns
        )
    ):
        return None
    curr_mid = _number(closed.iloc[-1], "kc_middle")
    prev_mid = _number(closed.iloc[-2], "kc_middle")
    prev2_mid = _number(closed.iloc[-3], "kc_middle") if len(closed) >= 3 else prev_mid
    curr_close = _number(closed.iloc[-1], "close")
    if None in (curr_mid, prev_mid, prev2_mid, curr_close):
        return None
    curr_ma5 = _number(closed.iloc[-1], "ma5") if "ma5" in closed.columns else None
    curr_ma15 = _number(closed.iloc[-1], "ma15") if "ma15" in closed.columns else None

    # Check for flat middle: if midline has no directional slope, it is FLAT / CHOP, not a trend!
    curr_atr = _number(closed.iloc[-1], "atr") or 0.0
    slope_atr = abs(curr_mid - prev_mid) / curr_atr if curr_atr > 0 else 0.0
    slope_pct = abs(curr_mid - prev2_mid) / curr_mid if curr_mid > 0 else 0.0
    if slope_atr < 0.05 and slope_pct < 0.0003:
        return None

    # Check for chop / MA5-MA15 entanglement in recent bars (relaxed for meme coins):
    if "ma5" in closed.columns and "ma15" in closed.columns and len(closed) >= 8:
        spread = closed["ma5"].tail(8) - closed["ma15"].tail(8)
        crossings = int((spread * spread.shift(1) < 0).sum())
        if crossings >= 4:
            return None

    if (
        curr_mid <= prev_mid
        and curr_mid < prev2_mid
        and curr_close < curr_mid
        and curr_ma5 is not None
        and curr_ma15 is not None
        and curr_ma5 < curr_ma15
        and _ltf_kc_wave_trend_conflict(closed, "SHORT") is None
    ):
        return "DOWNTREND"
    if (
        curr_mid >= prev_mid
        and curr_mid > prev2_mid
        and curr_close > curr_mid
        and curr_ma5 is not None
        and curr_ma15 is not None
        and curr_ma5 > curr_ma15
        and _ltf_kc_wave_trend_conflict(closed, "LONG") is None
    ):
        return "UPTREND"
    recent = closed.tail(25).reset_index(drop=True)
    last_lower_idx = None
    last_upper_idx = None
    for idx, row in recent.iterrows():
        low_v = _number(row, "low")
        kc_low_v = _number(row, "kc_lower")
        high_v = _number(row, "high")
        kc_up_v = _number(row, "kc_upper")
        if low_v is not None and kc_low_v is not None and float(low_v) <= float(kc_low_v) * 1.0005:
            last_lower_idx = int(idx)
        if high_v is not None and kc_up_v is not None and float(high_v) >= float(kc_up_v) * 0.9995:
            last_upper_idx = int(idx)
    if (
        last_lower_idx is not None
        and (last_upper_idx is None or last_lower_idx > last_upper_idx)
        and (curr_mid > prev_mid and curr_mid > prev2_mid)
        and (slope_atr >= 0.05 or slope_pct >= 0.0003)
        and curr_close > curr_mid
        and (curr_ma5 is None or curr_ma15 is None or curr_ma5 > curr_ma15)
        and _ltf_kc_wave_trend_conflict(closed, "LONG") is None
    ):
        return "UPTREND"
    if (
        last_upper_idx is not None
        and (last_lower_idx is None or last_upper_idx > last_lower_idx)
        and (curr_mid < prev_mid and curr_mid < prev2_mid)
        and (slope_atr >= 0.05 or slope_pct >= 0.0003)
        and curr_close < curr_mid
        and (curr_ma5 is None or curr_ma15 is None or curr_ma5 < curr_ma15)
        and _ltf_kc_wave_trend_conflict(closed, "SHORT") is None
    ):
        return "DOWNTREND"
    return None


def _is_ltf_wave_tail_end(
    closed: pd.DataFrame,
    side: str,
    quote_value: float,
    open_price: float,
    upper: float,
    lower: float,
) -> bool:
    """Return True if the 1m wave already hit its outer KC band and is rebounding in tail-end exhaustion."""
    if (
        not isinstance(closed, pd.DataFrame)
        or len(closed) < 4
        or not {"low", "high", "close", "open", "kc_lower", "kc_upper", "kc_middle"}.issubset(
            closed.columns
        )
    ):
        return False
    recent = closed.tail(4)
    closed_open = _number(closed.iloc[-1], "open")
    closed_close = _number(closed.iloc[-1], "close")
    if closed_open is None or closed_close is None:
        return False
    if side == "LONG":
        touched_upper = any(
            (
                _number(row, "high") is not None
                and _number(row, "kc_upper") is not None
                and float(_number(row, "high")) >= float(_number(row, "kc_upper")) * 0.9995
            )
            for _, row in recent.iterrows()
        )
        rebounding_down = closed_close <= closed_open or quote_value <= open_price
        return bool(touched_upper and rebounding_down)
    if side == "SHORT":
        touched_lower = any(
            (
                _number(row, "low") is not None
                and _number(row, "kc_lower") is not None
                and float(_number(row, "low")) <= float(_number(row, "kc_lower")) * 1.0005
            )
            for _, row in recent.iterrows()
        )
        rebounding_up = closed_close >= closed_open or quote_value >= open_price
        return bool(touched_lower and rebounding_up)
    return False


def _is_giant_or_exhausted_candle(
    closed: pd.DataFrame,
    side: str,
    quote_value: float,
    open_price: float,
    live_high: float,
    live_low: float,
    middle: float,
    half_width: float,
    ma15_current: float,
    previous_atr: float,
) -> tuple[bool, str]:
    """Symmetric filter preventing floor-chasing shorts and ceiling-chasing longs."""
    if previous_atr <= 0:
        return False, ""

    prev_bar = closed.iloc[-1] if isinstance(closed, pd.DataFrame) and len(closed) >= 1 else None
    prev_open = float(prev_bar["open"]) if prev_bar is not None and "open" in prev_bar else open_price
    prev_close = float(prev_bar["close"]) if prev_bar is not None and "close" in prev_bar else open_price
    prev_high = float(prev_bar["high"]) if prev_bar is not None and "high" in prev_bar else max(prev_open, prev_close)
    prev_low = float(prev_bar["low"]) if prev_bar is not None and "low" in prev_bar else min(prev_open, prev_close)
    prev_body = abs(prev_close - prev_open)

    if side == "SHORT":
        # ── 開空審查（防大長K、價格過低、地板追空）──
        # 1. 順向暴走大長陰棒（當根跌幅過大，價格已經砸到地板）
        live_drop = open_price - quote_value
        if live_drop >= 1.5 * previous_atr:
            return True, f"BLOCKED_GIANT_CANDLE_SHORT_PRICE_TOO_LOW: 當根跌幅({live_drop:.6f}) >= 1.5 ATR({previous_atr:.6f})，價格過低嚴禁地板追空"

        # 前根已收線陰棒實體過大且價格繼續破底
        prev_drop = prev_open - prev_close
        if prev_drop >= 1.2 * previous_atr and quote_value <= prev_close:
            return True, f"BLOCKED_PREV_GIANT_DROP_SHORT_PRICE_TOO_LOW: 前根實體跌幅({prev_drop:.6f}) >= 1.2 ATR，價格過低嚴禁地板追空"

        # 2. 反向強動能大長紅/陽K（多頭暴衝，嚴禁逆勢摸頂開空）
        live_surge = quote_value - open_price
        if live_surge >= 0.5 * previous_atr:
            return True, f"BLOCKED_COUNTER_BULL_CANDLE_SHORT: 當根大長紅陽K拉升({live_surge:.6f}) >= 0.5 ATR，嚴禁逆勢開空"

        prev_surge = prev_close - prev_open
        if prev_surge >= 0.8 * previous_atr:
            return True, f"BLOCKED_PREV_BULL_SURGE_SHORT: 前根大長紅陽K拉升({prev_surge:.6f}) >= 0.8 ATR，嚴禁逆勢開空"

        # 3. 底部長下影線衰竭托底（買盤強烈抵抗，見底訊號）
        prev_lower_wick = min(prev_open, prev_close) - prev_low
        if prev_lower_wick >= 0.6 * previous_atr and prev_lower_wick > prev_body:
            return True, f"BLOCKED_LOWER_WICK_EXHAUSTION_SHORT: 底部長下影線托底({prev_lower_wick:.6f}) > 實體，嚴禁地板追空"

        # 4. 均線與通道極度乖離（超跌過度延伸，隨時劇烈回抽）
        bias_ma15 = (ma15_current - quote_value) / previous_atr
        bias_kc_mid = (middle - quote_value) / half_width if half_width > 0 else 0.0
        if bias_ma15 > 2.5 or bias_kc_mid > 1.5:
            return True, f"BLOCKED_SHORT_BIAS_TOO_FAR_BELOW: 均線乖離({bias_ma15:.2f} ATR)過大，價格過低嚴禁地板追空"

    elif side == "LONG":
        # ── 開多審查（防大長K、價格過高、天花板追多）──
        # 1. 順向暴走大長陽棒（當根漲幅過大，價格已經拉到天花板）
        live_surge = quote_value - open_price
        if live_surge >= 1.5 * previous_atr:
            return True, f"BLOCKED_GIANT_CANDLE_LONG_PRICE_TOO_HIGH: 當根漲幅({live_surge:.6f}) >= 1.5 ATR({previous_atr:.6f})，價格過高嚴禁天花板追多"

        # 前根已收線陽棒實體過大且價格繼續創高
        prev_surge = prev_close - prev_open
        if prev_surge >= 1.2 * previous_atr and quote_value >= prev_close:
            return True, f"BLOCKED_PREV_GIANT_SURGE_LONG_PRICE_TOO_HIGH: 前根實體漲幅({prev_surge:.6f}) >= 1.2 ATR，價格過高嚴禁天花板追多"

        # 2. 反向強動能大長黑/陰K（空頭暴跌下殺，嚴禁接飛刀開多）
        live_drop = open_price - quote_value
        if live_drop >= 0.5 * previous_atr:
            return True, f"BLOCKED_COUNTER_BEAR_CANDLE_LONG: 當根大長黑陰K下殺({live_drop:.6f}) >= 0.5 ATR，嚴禁接飛刀開多"

        prev_drop = prev_open - prev_close
        if prev_drop >= 0.8 * previous_atr:
            return True, f"BLOCKED_PREV_BEAR_PLUNGE_LONG: 前根大長黑陰K下殺({prev_drop:.6f}) >= 0.8 ATR，嚴禁接飛刀開多"

        # 3. 頂部長上影線衰竭受阻（賣壓強烈壓制，見頂訊號）
        prev_upper_wick = prev_high - max(prev_open, prev_close)
        if prev_upper_wick >= 0.6 * previous_atr and prev_upper_wick > prev_body:
            return True, f"BLOCKED_UPPER_WICK_EXHAUSTION_LONG: 頂部長上影線受阻({prev_upper_wick:.6f}) > 實體，嚴禁天花板追多"

        # 4. 均線與通道極度乖離（超買過度延伸，隨時劇烈回調）
        bias_ma15 = (quote_value - ma15_current) / previous_atr
        bias_kc_mid = (quote_value - middle) / half_width if half_width > 0 else 0.0
        if bias_ma15 > 2.5 or bias_kc_mid > 1.5:
            return True, f"BLOCKED_LONG_BIAS_TOO_FAR_ABOVE: 均線乖離({bias_ma15:.2f} ATR)過大，價格過高嚴禁天花板追多"

    return False, ""


def _has_ltf_outer_band_valley_or_peak_turn(
    closed: pd.DataFrame, htf_state: str,
) -> bool:
    """Return whether 1m recently rebounded from an outer-band valley/peak into trend."""
    detected = _detect_ltf_outer_band_turn_state(closed)
    if detected is not None:
        return detected == htf_state
    if (
        not isinstance(closed, pd.DataFrame)
        or len(closed) < 3
        or not {"low", "high", "close", "kc_lower", "kc_upper", "kc_middle"}.issubset(
            closed.columns
        )
    ):
        return False
    recent = closed.tail(12)
    curr_mid = _number(closed.iloc[-1], "kc_middle")
    prev_mid = _number(closed.iloc[-2], "kc_middle")
    curr_close = _number(closed.iloc[-1], "close")
    if None in (curr_mid, prev_mid, curr_close):
        return False
    curr_atr = _number(closed.iloc[-1], "atr") or 0.0
    slope_atr = abs(curr_mid - prev_mid) / curr_atr if curr_atr > 0 else 0.0
    if slope_atr < 0.05:
        return False
    if htf_state == "UPTREND":
        touched_lower = any(
            (
                _number(row, "low") is not None
                and _number(row, "kc_lower") is not None
                and float(_number(row, "low")) <= float(_number(row, "kc_lower")) * 1.0005
            )
            for _, row in recent.iterrows()
        )
        return bool(touched_lower and curr_mid > prev_mid and curr_close > curr_mid)
    if htf_state == "DOWNTREND":
        touched_upper = any(
            (
                _number(row, "high") is not None
                and _number(row, "kc_upper") is not None
                and float(_number(row, "high")) >= float(_number(row, "kc_upper")) * 0.9995
            )
            for _, row in recent.iterrows()
        )
        return bool(touched_upper and curr_mid < prev_mid and curr_close < curr_mid)
    return False


def evaluate_closed_15m_kc_trend(frame_15m: Any) -> dict[str, Any]:
    data = _valid_live_frame(frame_15m, 900_000.0)
    if data is None:
        return {
            "valid": False,
            "direction": "UNKNOWN",
            "reason": "BLOCKED_15M_KC_CLOSED_TREND_DATA_INVALID",
        }
    closed, _ = data
    direction = _closed_kc_direction(closed)
    if direction == "FLAT":
        htf = evaluate_htf_kc_state(frame_15m)
        if (
            htf.get("valid")
            and htf.get("state") in ("UPTREND", "DOWNTREND")
            and htf.get("closed_midline_direction") == htf.get("state")
        ):
            direction = str(htf["state"])
    if direction == "FLAT":
        return {
            "valid": True,
            "direction": direction,
            "reason": "WAIT_15M_KC_TWO_CLOSED_DIRECTIONS",
        }
    return {"valid": True, "direction": direction, "reason": "PASS"}


def _kc_midline_entry_problem(frame: Any, timeframe_ms: float, side: str, prefix: str):
    data = _valid_live_frame(frame, timeframe_ms)
    if data is None:
        return f"BLOCKED_{prefix}_KC_MIDLINE_DATA_INVALID"
    closed, _ = data
    if len(closed) < 3:
        return f"BLOCKED_{prefix}_KC_MIDLINE_HISTORY_INVALID"

    middle = [_number(closed.iloc[index], "kc_middle") for index in (-3, -2, -1)]
    atr = _number(closed.iloc[-1], "atr")
    if (
        any(value is None or value <= 0 for value in middle)
        or atr is None
        or atr <= 0
    ):
        return f"BLOCKED_{prefix}_KC_MIDLINE_DATA_INVALID"

    normalized_side = str(side or "").upper()
    delta = float(middle[2]) - float(middle[1])
    if (
        normalized_side == "LONG" and delta <= 0
        or normalized_side == "SHORT" and delta >= 0
        or normalized_side not in ("LONG", "SHORT")
    ):
        return f"BLOCKED_{prefix}_KC_MIDLINE_NOT_DIRECTIONAL"

    slope_atr = abs(delta) / float(atr)
    slope_pct = abs(float(middle[2]) - float(middle[0])) / abs(float(middle[2]))
    if (
        slope_atr < 0.05
        and not math.isclose(slope_atr, 0.05, rel_tol=1e-9, abs_tol=0.0)
    ) and (
        slope_pct < 0.0003
        and not math.isclose(slope_pct, 0.0003, rel_tol=1e-9, abs_tol=0.0)
    ):
        return f"BLOCKED_{prefix}_KC_MIDLINE_FLAT"
    return None


def mtf_entry_midline_problem(frame_1m: Any, frame_15m: Any, side: str):
    ltf_problem = _kc_midline_entry_problem(frame_1m, 60_000.0, side, "LTF")
    if ltf_problem is not None:
        return ltf_problem
    expected_trend = "UPTREND" if str(side or "").upper() == "LONG" else "DOWNTREND"
    data_1m = _valid_live_frame(frame_1m, 60_000.0)
    if data_1m is not None and _detect_ltf_outer_band_turn_state(data_1m[0]) == expected_trend:
        return None
    htf = evaluate_htf_kc_state(frame_15m)
    if (
        htf.get("valid")
        and htf.get("state") == expected_trend
        and htf.get("closed_midline_direction") == expected_trend
    ):
        return None
    return _kc_midline_entry_problem(frame_15m, 900_000.0, side, "HTF")


def close_in_current_live_1m_bar(frame: Any, closed_at: Any) -> Optional[bool]:
    """Return whether the latest close happened in the current live 1m candle."""
    if closed_at is None:
        return False
    data = _valid_live_frame(frame, 60_000.0)
    if data is None:
        return None
    _closed, live = data
    live_start_ms = _timestamp_ms(live.get("timestamp"))
    close_time_ms = _timestamp_ms(closed_at)
    if live_start_ms is None or close_time_ms is None:
        return None
    if int(live_start_ms // 60_000) == int(close_time_ms // 60_000):
        return True
    if close_time_ms >= live_start_ms:
        return None
    return False


def mtf_cycle_reverse_reentry_ticket_valid(
    account: Any, symbol: str, side: str, frame: Any,
) -> bool:
    """Validate the one-shot same-bar re-entry claim created by an MTF cycle flip."""
    side = str(side or "").upper()
    if side not in ("LONG", "SHORT"):
        return False
    entry_state = getattr(account, "entry_filter_last", None)
    tickets = (
        entry_state.get("mtf_cycle_reverse_pending")
        if isinstance(entry_state, dict) else None
    )
    ticket = tickets.get(symbol) if isinstance(tickets, dict) else None
    if not isinstance(ticket, Mapping):
        return False

    positions = getattr(account, "positions", None)
    current_position = positions.get(symbol) if isinstance(positions, dict) else None
    if isinstance(current_position, Mapping):
        amount = current_position.get("amount", current_position.get("qty"))
        if amount is None:
            return False
        try:
            if abs(float(amount)) > 0:
                return False
        except (TypeError, ValueError, OverflowError):
            return False

    source_side = str(
        getattr(account, "last_closed_side", {}).get(symbol) or "",
    ).upper()
    actual_close = getattr(account, "last_closed_at", {}).get(symbol)
    expected_target = "SHORT" if source_side == "LONG" else "LONG"
    expected_reasons = {
        "MTF_KC_STATE_MACHINE_DOWNTREND_REVERSAL_EXIT"
        if source_side == "LONG"
        else "MTF_KC_STATE_MACHINE_UPTREND_REVERSAL_EXIT",
        "EXIT_BY_EXTREME_WATERFALL",
    }
    try:
        actual_close_value = float(actual_close)
        ticket_close_value = float(ticket.get("close_timestamp"))
        ticket_bar_ms = _timestamp_ms(ticket.get("bar_id"))
        current_bar_ms = _timestamp_ms(frame.iloc[-1].get("timestamp"))
    except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
        return False
    if (
        source_side not in ("LONG", "SHORT")
        or side != expected_target
        or str(ticket.get("source_side") or "").upper() != source_side
        or str(ticket.get("target_side") or "").upper() != side
        or ticket.get("reason") not in expected_reasons
        or not math.isfinite(actual_close_value)
        or not math.isfinite(ticket_close_value)
        or not math.isclose(
            actual_close_value, ticket_close_value, rel_tol=0, abs_tol=0.001,
        )
        or ticket_bar_ms is None
        or current_bar_ms is None
        or int(ticket_bar_ms // 60_000) != int(current_bar_ms // 60_000)
        or close_in_current_live_1m_bar(frame, actual_close) is not True
    ):
        return False
    return True


def evaluate_mtf_exit_confirmation(
    frame_1m: Any,
    position: Mapping[str, Any],
    metadata: dict[str, Any],
    reason: str | None,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist ordinary MTF exits until the trigger bar closes against the position."""
    side = str(position.get("side") or "").upper()
    opened_at_ms = _timestamp_ms(position.get("open_timestamp"))
    pending_key = "mtf_pending_closed_bar_exit"
    pending = metadata.get(pending_key)
    changed = False
    normalized_reason = str(reason or "").upper()
    immediate = _is_mtf_emergency_exit(normalized_reason)
    if immediate and reason:
        changed = metadata.pop(pending_key, None) is not None
        return {
            "allowed": True,
            "waiting": False,
            "changed": changed,
            "reason": reason,
            "evidence": dict(evidence or {}),
        }
    if reason and not _is_mtf_cycle_exit(reason):
        return {
            "allowed": False,
            "waiting": False,
            "changed": False,
            "reason": "BLOCKED_MTF_EXIT_REASON_NOT_AUTHORIZED",
        }

    if side not in ("LONG", "SHORT") or opened_at_ms is None:
        return {
            "allowed": False,
            "waiting": True,
            "changed": False,
            "reason": "BLOCKED_MTF_EXIT_POSITION_IDENTITY_INVALID",
        }

    if isinstance(pending, Mapping):
        pending_open_ms = _timestamp_ms(pending.get("position_open_timestamp"))
        pending_bar_ms = _timestamp_ms(pending.get("bar_id"))
        if (
            str(pending.get("side") or "").upper() != side
            or pending_open_ms is None
            or not math.isclose(pending_open_ms, opened_at_ms, rel_tol=0, abs_tol=1)
            or pending_bar_ms is None
            or not _is_mtf_cycle_exit(pending.get("reason"))
        ):
            metadata.pop(pending_key, None)
            pending = None
            changed = True

    pending_reason = pending.get("reason") if isinstance(pending, Mapping) else None
    cycle_reason = reason if _is_mtf_cycle_exit(reason) else pending_reason
    if _is_mtf_cycle_exit(cycle_reason):
        ltf_direction = evaluate_ltf_kc_midline_direction(frame_1m)
        if not ltf_direction.get("valid"):
            return {
                "allowed": False,
                "waiting": True,
                "changed": changed,
                "reason": ltf_direction["reason"],
            }
        expected_reversal = "DOWNTREND" if side == "LONG" else "UPTREND"
        if ltf_direction.get("direction") != expected_reversal:
            changed = metadata.pop(pending_key, None) is not None or changed
            return {
                "allowed": False,
                "waiting": False,
                "changed": changed,
                "reason": "VETO_MTF_LTF_MIDLINE_DIRECTION_UNCONFIRMED",
                "evidence": {
                    "ltf_closed_midline_direction": ltf_direction.get("direction"),
                    "required_direction": expected_reversal,
                },
            }

    if _is_mtf_cycle_exit(reason):
        expected_reversal = "DOWNTREND" if side == "LONG" else "UPTREND"
        confirmed_direction = (
            (evidence or {}).get("closed_midline_direction")
            or (evidence or {}).get("closed_trend")
        )
        if (
            (evidence or {}).get("valid") is True
            and confirmed_direction == expected_reversal
        ):
            changed = metadata.pop(pending_key, None) is not None
            return {
                "allowed": True,
                "waiting": False,
                "changed": changed,
                "reason": reason,
                "evidence": dict(evidence or {}),
            }

    if isinstance(pending, Mapping) and pending.get("confirmed") is True:
        return {
            "allowed": True,
            "waiting": False,
            "changed": False,
            "reason": str(pending["reason"]),
            "evidence": dict(pending.get("evidence") or {}),
        }

    data = _valid_live_frame(frame_1m, 60_000.0)
    if data is None:
        return {
            "allowed": False,
            "waiting": True,
            "changed": False,
            "reason": "BLOCKED_MTF_EXIT_CONFIRMATION_DATA_INVALID",
        }
    closed, live = data
    live_bar_ms = _timestamp_ms(live.get("timestamp"))
    if live_bar_ms is None or live_bar_ms + 60_000 <= opened_at_ms:
        return {
            "allowed": False,
            "waiting": True,
            "changed": False,
            "reason": "BLOCKED_MTF_EXIT_BAR_ID_INVALID",
        }

    if isinstance(pending, Mapping):
        pending_bar_ms = _timestamp_ms(pending.get("bar_id"))
        if pending_bar_ms is None or live_bar_ms < pending_bar_ms:
            return {
                "allowed": False,
                "waiting": True,
                "changed": False,
                "reason": "BLOCKED_MTF_EXIT_BAR_SEQUENCE_INVALID",
            }
        if live_bar_ms > pending_bar_ms:
            matching_bar = None
            for _, candle in closed.iterrows():
                candle_ms = _timestamp_ms(candle.get("timestamp"))
                if candle_ms is not None and math.isclose(
                    candle_ms, pending_bar_ms, rel_tol=0, abs_tol=1,
                ):
                    matching_bar = candle
                    break
            if matching_bar is None:
                return {
                    "allowed": False,
                    "waiting": True,
                    "changed": False,
                    "reason": "BLOCKED_MTF_EXIT_CONFIRMATION_BAR_MISSING",
                }
            opening = _number(matching_bar, "open")
            close = _number(matching_bar, "close")
            if (
                opening is None or close is None or opening <= 0 or close <= 0
                or pending_bar_ms + 60_000 <= opened_at_ms
            ):
                return {
                    "allowed": False,
                    "waiting": True,
                    "changed": False,
                    "reason": "BLOCKED_MTF_EXIT_CONFIRMATION_OHLC_INVALID",
                }
            color_adverse = close < opening if side == "LONG" else close > opening
            mid_val = _number(matching_bar, "kc_middle")
            ma15_val = _number(matching_bar, "ma15")
            structural_adverse = False
            if side == "SHORT":
                if mid_val is not None and ma15_val is not None:
                    structural_adverse = (close > min(mid_val, ma15_val))
                elif mid_val is not None:
                    structural_adverse = (close > mid_val)
                else:
                    structural_adverse = color_adverse
            elif side == "LONG":
                if mid_val is not None and ma15_val is not None:
                    structural_adverse = (close < max(mid_val, ma15_val))
                elif mid_val is not None:
                    structural_adverse = (close < mid_val)
                else:
                    structural_adverse = color_adverse

            adverse_close = color_adverse and structural_adverse
            if adverse_close:
                confirmed = dict(pending)
                confirmed["confirmed"] = True
                confirmed["evidence"] = {
                    **dict(confirmed.get("evidence") or {}),
                    "confirmation_bar_id": pending_bar_ms,
                    "confirmation_open": opening,
                    "confirmation_close": close,
                }
                metadata[pending_key] = confirmed
                return {
                    "allowed": True,
                    "waiting": False,
                    "changed": True,
                    "reason": str(confirmed["reason"]),
                    "evidence": dict(confirmed["evidence"]),
                }
            metadata.pop(pending_key, None)
            pending = None
            changed = True
        else:
            return {
                "allowed": False,
                "waiting": True,
                "changed": False,
                "reason": "WAIT_MTF_EXIT_CLOSED_BAR_COLOR",
            }
    if not reason:
        return {
            "allowed": False,
            "waiting": False,
            "changed": changed,
            "reason": None,
        }

    metadata[pending_key] = {
        "reason": reason,
        "side": side,
        "position_open_timestamp": position.get("open_timestamp"),
        "bar_id": live.get("timestamp"),
        "evidence": dict(evidence or {}),
        "confirmed": False,
    }
    return {
        "allowed": False,
        "waiting": True,
        "changed": True,
        "reason": "WAIT_MTF_EXIT_CLOSED_BAR_COLOR",
    }


def evaluate_htf_kc_state(frame_15m: Any) -> dict[str, Any]:
    """Classify 15m KC direction from the current, still-forming candle."""
    from core.services.low_latency_exit import unwrap_frame_ref

    if not isinstance(frame_15m, pd.DataFrame):
        frame_15m = unwrap_frame_ref(frame_15m)
    data = _valid_live_frame(frame_15m, 900_000.0)
    if data is None:
        return {"valid": False, "reason": "BLOCKED_HTF_KC_LIVE_DATA_INVALID"}
    closed, current = data
    previous = frame_15m.iloc[-2] if isinstance(frame_15m, pd.DataFrame) and len(frame_15m) >= 2 else closed.iloc[-1]
    live_ohlc = tuple(_number(current, key) for key in ("open", "high", "low", "close"))
    middle_current = _number(current, "kc_middle")
    middle_previous = _number(previous, "kc_middle")
    atr_current = _number(current, "atr")
    upper = _number(current, "kc_upper")
    lower = _number(current, "kc_lower")
    if (
        any(value is None for value in live_ohlc)
        or live_ohlc[1] < max(live_ohlc[0], live_ohlc[3])
        or live_ohlc[2] > min(live_ohlc[0], live_ohlc[3])
        or middle_current is None or middle_previous is None or atr_current is None
        or upper is None or lower is None or atr_current <= 0
        or not lower < middle_current < upper
    ):
        return {"valid": False, "reason": "BLOCKED_HTF_KC_INDICATORS_INVALID"}

    normalized_slope = (middle_current - middle_previous) / atr_current
    state = (
        "UPTREND" if normalized_slope > SLOPE_THRESHOLD
        else "DOWNTREND" if normalized_slope < -SLOPE_THRESHOLD
        else "FLAT"
    )
    closed_midline_direction = None
    closed_middle = None
    previous_closed_middle = None
    if len(closed) >= 2:
        closed_middle = _number(closed.iloc[-1], "kc_middle")
        previous_closed_middle = _number(closed.iloc[-2], "kc_middle")
        if (
            closed_middle is not None
            and previous_closed_middle is not None
            and closed_middle > 0
            and previous_closed_middle > 0
        ):
            if math.isclose(
                closed_middle, previous_closed_middle, rel_tol=1e-12, abs_tol=0,
            ):
                closed_midline_direction = "FLAT"
            else:
                closed_midline_direction = (
                    "UPTREND"
                    if closed_middle > previous_closed_middle
                    else "DOWNTREND"
                )
    closed_trend = _closed_kc_direction(closed)
    if (
        closed_trend == "FLAT"
        and state in ("UPTREND", "DOWNTREND")
        and closed_midline_direction == state
    ):
        closed_trend = state
    return {
        "valid": True,
        "state": state,
        "closed_trend": closed_trend,
        "closed_midline_direction": closed_midline_direction,
        "closed_kc_middle": closed_middle,
        "previous_closed_kc_middle": previous_closed_middle,
        "normalized_slope": normalized_slope,
        "kc_middle": middle_current,
        "kc_upper": upper,
        "kc_lower": lower,
        "atr": atr_current,
        "bar_timestamp": _number(current, "timestamp"),
    }


MTF_KC_BAND_TREND_ENTRY = "MTF_KC_BAND_TREND_ENTRY"
BAND_TREND_MAX_CHASE_ATR = 0.5


def kc_band_trend_side(frame_1m: Any, htf: Mapping[str, Any]) -> Optional[str]:
    """LONG when 15m KC is up and the closed 1m KC upper band rises; SHORT mirrors.

    User authority (2026-11-18): 上軌向上 + 大週期向上就開多，下軌向下 + 大週期向下就開空。
    """
    if not isinstance(htf, Mapping) or not htf.get("valid"):
        return None
    state = htf.get("state")
    closed_trend = htf.get("closed_trend")
    data = _valid_live_frame(frame_1m, 60_000.0)
    if data is None:
        return None
    closed = data[0]
    if len(closed) < 2 or not {"kc_upper", "kc_lower", "kc_middle"}.issubset(closed.columns):
        return None
    upper_now, upper_prev = _number(closed.iloc[-1], "kc_upper"), _number(closed.iloc[-2], "kc_upper")
    lower_now, lower_prev = _number(closed.iloc[-1], "kc_lower"), _number(closed.iloc[-2], "kc_lower")
    if None in (upper_now, upper_prev, lower_now, lower_prev):
        return None
    htf_up = state == "UPTREND" or (closed_trend == "UPTREND" and state != "DOWNTREND")
    htf_down = state == "DOWNTREND" or (closed_trend == "DOWNTREND" and state != "UPTREND")
    if htf_up and upper_now > upper_prev:
        return "LONG"
    if htf_down and lower_now < lower_prev:
        return "SHORT"
    return None


def _evaluate_kc_band_trend_entry(
    frame_1m: Any, htf: Mapping[str, Any], quote: float, side: str | None,
) -> Optional[dict[str, Any]]:
    band_side = kc_band_trend_side(frame_1m, htf)
    if band_side is None:
        return None
    if str(side or "").upper() in ("LONG", "SHORT") and str(side).upper() != band_side:
        return None
    if getattr(frame_1m, "attrs", {}).get("entry_finality_verified") is not True:
        return None
    closed, live = _valid_live_frame(frame_1m, 60_000.0)
    try:
        quote_value = float(quote)
    except (TypeError, ValueError, OverflowError):
        return None
    last = closed.iloc[-1]
    middle = _number(last, "kc_middle")
    upper = _number(last, "kc_upper")
    lower = _number(last, "kc_lower")
    atr = _number(last, "atr")
    open_price = _number(live, "open")
    if (
        not math.isfinite(quote_value) or quote_value <= 0
        or None in (middle, upper, lower, atr, open_price)
        or atr <= 0 or not lower < middle < upper
    ):
        return None
    is_long = band_side == "LONG"
    # Price must sit on the trend side of the 1m KC midline.
    if (is_long and quote_value <= middle) or (not is_long and quote_value >= middle):
        return None
    # Anti-chase: never further than 0.5 ATR outside the same-side outer band.
    edge_offset = quote_value - upper if is_long else lower - quote_value
    if edge_offset > BAND_TREND_MAX_CHASE_ATR * atr:
        return None
    half_width = middle - lower
    ma5 = _number(last, "ma5")
    ma15 = _number(last, "ma15")
    previous_ma5 = _number(closed.iloc[-2], "ma5")
    return {
        **htf,
        "allowed": True,
        "reason": MTF_KC_BAND_TREND_ENTRY,
        "side": band_side,
        "price": quote_value,
        "open": open_price,
        "ma5": ma5,
        "previous_ma5": previous_ma5,
        "ma15": ma15,
        "kc_middle_1m": middle,
        "kc_upper_1m": upper,
        "kc_lower_1m": lower,
        "half_width_1m": half_width,
        "entry_band_ratio": (
            (quote_value - middle) if is_long else (middle - quote_value)
        ) / half_width if half_width > 0 else 0.0,
        "ltf_atr": atr,
        "bandwidth_pct": (upper - lower) / middle,
        "breakout_edge_offset": max(edge_offset, 0.0),
    }


def evaluate_mtf_entry(
    frame_1m: Any,
    frame_15m: Any,
    *,
    quote: float,
    side: str | None = None,
    pending_candidate: Mapping[str, Any] | None = None,
    allow_colorless_reverse: bool = False,
) -> dict[str, Any]:
    """Evaluate an entry inside a directional HTF KC cycle without side effects."""
    from core.services.ma5_entry_slope import quote_adjusted_entry_mas

    htf = evaluate_htf_kc_state(frame_15m)
    if not htf.get("valid"):
        return {**htf, "allowed": False, "reason": htf["reason"]}
    if not allow_colorless_reverse:
        band_entry = _evaluate_kc_band_trend_entry(frame_1m, htf, quote, side)
        if band_entry is not None:
            return band_entry
    data = _valid_live_frame(frame_1m, 60_000.0)
    closed_trend = htf.get("closed_trend")
    ltf_turn_state = (
        _detect_ltf_outer_band_turn_state(data[0]) if data is not None else None
    )
    ltf_turn_override = False
    if ltf_turn_state in ("UPTREND", "DOWNTREND"):
        closed_trend = ltf_turn_state
        htf = {**htf, "state": ltf_turn_state, "closed_trend": ltf_turn_state}
        ltf_turn_override = True
    elif (
        closed_trend not in ("UPTREND", "DOWNTREND")
        and htf.get("state") in ("UPTREND", "DOWNTREND")
    ):
        if htf.get("closed_midline_direction") == htf.get("state") or (
            data is not None
            and _has_ltf_outer_band_valley_or_peak_turn(
                data[0], str(htf["state"]),
            )
        ):
            closed_trend = str(htf["state"])
            htf = {**htf, "closed_trend": closed_trend}
    if closed_trend not in ("UPTREND", "DOWNTREND"):
        return {
            **htf,
            "allowed": False,
            "reason": "BLOCKED_MTF_KC_TWO_CLOSED_DIRECTIONS_NOT_CONFIRMED",
        }
    expected_side = "LONG" if closed_trend == "UPTREND" else "SHORT"
    if str(side or "").upper() in ("LONG", "SHORT") and str(side).upper() != expected_side:
        return {
            **htf,
            "allowed": False,
            "reason": f"BLOCKED_MTF_KC_COUNTERTREND_{str(side).upper()}",
        }
    if data is None or getattr(frame_1m, "attrs", {}).get("entry_finality_verified") is not True:
        return {
            **htf,
            "allowed": False,
            "reason": "BLOCKED_LTF_KC_LIVE_DATA_INVALID",
        }
    closed, live = data
    midline_problem = mtf_entry_midline_problem(
        frame_1m, frame_15m, expected_side,
    )
    if midline_problem is not None:
        return {**htf, "allowed": False, "reason": midline_problem}
    closed_open = _number(closed.iloc[-1], "open")
    closed_close = _number(closed.iloc[-1], "close")
    expected_closed_color = expected_side
    colorless_reverse = (
        allow_colorless_reverse and str(side or "").upper() in ("LONG", "SHORT")
    )
    if (
        closed_open is None or closed_close is None
        or closed_open <= 0 or closed_close <= 0
    ):
        return {
            **htf,
            "allowed": False,
            "reason": "BLOCKED_MTF_LTF_CLOSED_BAR_INVALID",
            "closed_bar_id": _number(closed.iloc[-1], "timestamp"),
        }
    if len(closed) < 15:
        return {**htf, "allowed": False, "reason": "BLOCKED_LTF_MA_HISTORY_INVALID"}
    wave_conflict = _ltf_kc_wave_trend_conflict(closed, expected_side)
    if wave_conflict is not None:
        return {**htf, "allowed": False, "reason": wave_conflict}
    has_ltf_kc_bands = {"kc_upper", "kc_lower", "kc_middle"}.issubset(closed.columns)
    if has_ltf_kc_bands:
        if "ma5" in closed.columns and "ma15" in closed.columns and len(closed) >= 8:
            spread = closed["ma5"].tail(8) - closed["ma15"].tail(8)
            if int((spread * spread.shift(1) < 0).sum()) >= 4:
                return {
                    **htf,
                    "allowed": False,
                    "reason": "BLOCKED_MTF_KC_CONSOLIDATION:VETO_MA5_MA15_CHOP",
                }

    from core.services.market_consolidation_filter import evaluate_market_blacklist

    consolidation_blocked, consolidation_reason, consolidation_evidence = (
        evaluate_market_blacklist(
            closed,
            side=expected_closed_color,
            cooldown_bars=0,
            independent_kc_direction=True,
            meme_policy=True,
            meme_low_trend_override=True,
            meme_structural_entry=True,
            strict_consolidation=True,
            confirmed_flat_kc_structure=True,
        )
    )
    if consolidation_blocked:
        return {
            **htf,
            "allowed": False,
            "reason": f"BLOCKED_MTF_KC_CONSOLIDATION:{consolidation_reason}",
            "consolidation_evidence": consolidation_evidence,
        }
    try:
        quote_value = float(quote)
    except (TypeError, ValueError, OverflowError):
        quote_value = math.nan
    if not math.isfinite(quote_value) or quote_value <= 0:
        return {**htf, "allowed": False, "reason": "BLOCKED_LIVE_QUOTE_INVALID"}

    normalized_side = str(side or "").upper()
    has_ltf_kc_bands = {"kc_upper", "kc_lower", "kc_middle"}.issubset(closed.columns)
    midline = (
        float(_number(closed.iloc[-1], "kc_middle") or htf["kc_middle"])
        if ltf_turn_override
        else float(htf["kc_middle"])
    )
    midline_hold_signal = False
    if normalized_side == expected_side and len(closed) >= 2:
        previous_close = _number(closed.iloc[-2], "close")
        latest_close = _number(closed.iloc[-1], "close")
        live_open_check = _number(live, "open")
        if (
            previous_close is not None
            and latest_close is not None
            and previous_close > 0
            and latest_close > 0
        ):
            color_ok_for_hold = True
            if has_ltf_kc_bands and live_open_check is not None:
                color_ok_for_hold = (
                    quote_value > live_open_check
                    if expected_side == "LONG"
                    else quote_value < live_open_check
                )
            midline_hold_signal = color_ok_for_hold and (
                previous_close > midline
                and latest_close > midline
                and quote_value > midline
                if expected_side == "LONG"
                else previous_close < midline
                and latest_close < midline
                and quote_value < midline
            )
    if (
        not colorless_reverse
        and not midline_hold_signal
        and (
            (expected_closed_color == "LONG" and closed_close <= closed_open)
            or (expected_closed_color == "SHORT" and closed_close >= closed_open)
        )
    ):
        return {
            **htf,
            "allowed": False,
            "reason": "BLOCKED_MTF_LTF_CLOSED_BAR_COLOR",
            "closed_bar_id": _number(closed.iloc[-1], "timestamp"),
        }
    if colorless_reverse or midline_hold_signal:
        pending_candidate = None

    ma_values = quote_adjusted_entry_mas(frame_1m, quote_value)
    if ma_values is None:
        return {**htf, "allowed": False, "reason": "BLOCKED_LIVE_MA_DATA_INVALID"}
    ma5_current, ma5_previous, ma15_current = map(float, ma_values)
    open_price = _number(live, "open")
    live_high = _number(live, "high")
    live_low = _number(live, "low")
    live_close = _number(live, "close")
    previous_atr = _number(closed.iloc[-1], "atr")
    live_ohlc = {
        "open": open_price,
        "high": live_high,
        "low": live_low,
        "close": live_close,
    }
    try:
        live_ohlc["close"] = quote_value
        live_ohlc["high"] = max(float(live_ohlc["high"]), quote_value)
        live_ohlc["low"] = min(float(live_ohlc["low"]), quote_value)
    except (TypeError, ValueError, OverflowError):
        return {**htf, "allowed": False, "reason": "BLOCKED_LIVE_OHLC_INVALID"}
    live_frame = pd.concat(
        [closed[["open", "high", "low", "close"]], pd.DataFrame([live_ohlc])],
        ignore_index=True,
    )
    from core.services.technical_indicators import compute_keltner_channels

    kc = compute_keltner_channels(live_frame, ema_period=20, atr_period=14, multiplier=1.5)
    middle = _number(kc.iloc[-1], "kc_middle")
    middle_previous = _number(kc.iloc[-2], "kc_middle") if len(kc) >= 2 else None
    upper = _number(kc.iloc[-1], "kc_upper")
    lower = _number(kc.iloc[-1], "kc_lower")
    values = (
        open_price, live_high, live_low, live_close, middle, middle_previous,
        upper, lower, previous_atr,
    )
    if (
        any(value is None for value in values)
        or live_high < max(open_price, live_close)
        or live_low > min(open_price, live_close)
        or open_price <= 0 or middle <= 0 or lower <= 0 or upper <= lower
        or middle_previous <= 0 or not lower < middle < upper or previous_atr <= 0
    ):
        return {**htf, "allowed": False, "reason": "BLOCKED_LTF_KC_INDICATORS_INVALID"}

    from core.services.entry_contract import ma5_ma15_kc_midline_entry_problem
    alignment_problem = ma5_ma15_kc_midline_entry_problem(
        expected_side, ma5_current, ma15_current, middle, previous_atr,
        middle_previous,
    )
    if alignment_problem:
        return {**htf, "allowed": False, "reason": alignment_problem}

    half_width = middle - lower
    bandwidth_pct = (upper - lower) / middle
    if half_width <= 0:
        return {
            **htf,
            "allowed": False,
            "reason": "BLOCKED_LTF_KC_EXTREME_NARROW",
            "bandwidth_pct": bandwidth_pct,
        }

    sides = (normalized_side,) if normalized_side in ("LONG", "SHORT") else ("LONG", "SHORT")
    live_bar_id = _number(live, "timestamp")
    deferred_wait: dict[str, Any] | None = None
    for candidate_side in sides:
        is_long = candidate_side == "LONG"
        expected_state = "UPTREND" if is_long else "DOWNTREND"
        if closed_trend != expected_state:
            continue
        distance = quote_value - middle if is_long else middle - quote_value
        ratio = distance / half_width

        if is_long:
            ma_slope_aligned = ma5_current > ma5_previous
            candle_aligned = (
                colorless_reverse or midline_hold_signal or quote_value > open_price
            )
            breakout_edge_offset = quote_value - upper if quote_value > upper else 0.0
            breakout_allowed = (
                quote_value > upper
                and 0.0 < breakout_edge_offset <= 0.80 * half_width
                and ma_slope_aligned
            )
        else:
            ma_slope_aligned = ma5_current < ma5_previous
            candle_aligned = (
                colorless_reverse or midline_hold_signal or quote_value < open_price
            )
            breakout_edge_offset = lower - quote_value if quote_value < lower else 0.0
            breakout_allowed = (
                quote_value < lower
                and 0.0 < breakout_edge_offset <= 0.80 * half_width
                and ma_slope_aligned
            )

        location_allowed = -1.0 < ratio < (
            1.5 if (colorless_reverse and has_ltf_kc_bands)
            else 1.05 if has_ltf_kc_bands
            else MAX_ENTRY_BAND_RATIO
        )

        pending_matches_next_bar = False
        pending_source_state = ""
        if (
            isinstance(pending_candidate, Mapping)
            and str(pending_candidate.get("side") or "").upper() == candidate_side
            and live_bar_id is not None
        ):
            try:
                pending_source_bar = float(pending_candidate.get("source_bar_id"))
                pending_source_state = str(pending_candidate.get("state") or "").upper()
                pending_matches_next_bar = (
                    math.isfinite(pending_source_bar)
                    and math.isclose(
                        live_bar_id - pending_source_bar,
                        60_000.0,
                        rel_tol=0,
                        abs_tol=1,
                    )
                )
            except (TypeError, ValueError, OverflowError):
                pending_matches_next_bar = False

        if pending_matches_next_bar and not breakout_allowed:
            state_still_valid = (
                closed_trend == expected_state
                and pending_source_state == expected_state
            )
            if (
                state_still_valid
                and location_allowed
                and ma_slope_aligned
            ):
                if candle_aligned:
                    giant_blocked, giant_reason = _is_giant_or_exhausted_candle(
                        closed, candidate_side, quote_value, open_price,
                        live_high, live_low, middle, half_width, ma15_current, previous_atr,
                    )
                    if giant_blocked:
                        return {
                            **htf,
                            "allowed": False,
                            "reason": giant_reason,
                            "side": candidate_side,
                        }
                    return {
                        **htf,
                        "allowed": True,
                        "reason": "MTF_KC_DEFERRED_ENTRY",
                        "side": candidate_side,
                        "price": quote_value,
                        "open": open_price,
                        "ma5": ma5_current,
                        "previous_ma5": ma5_previous,
                        "ma15": ma15_current,
                        "kc_middle_1m": middle,
                        "kc_upper_1m": upper,
                        "kc_lower_1m": lower,
                        "half_width_1m": half_width,
                        "entry_band_ratio": ratio,
                        "ltf_atr": previous_atr,
                        "bandwidth_pct": bandwidth_pct,
                        "deferred_candidate": dict(pending_candidate),
                    }
                deferred_wait = {
                    **htf,
                    "allowed": False,
                    "reason": "WAIT_MTF_KC_DEFERRED_NEXT_BAR_COLOR",
                    "side": candidate_side,
                    "price": quote_value,
                    "open": open_price,
                    "ma5": ma5_current,
                    "previous_ma5": ma5_previous,
                    "ma15": ma15_current,
                    "kc_middle_1m": middle,
                    "entry_band_ratio": ratio,
                    "deferred_candidate": dict(pending_candidate),
                }

        # A fresh outside-band breakout is evaluated immediately without
        # requiring candle color, while retaining the extension cap.
        if not breakout_allowed and (not location_allowed or not ma_slope_aligned):
            continue

        if not candle_aligned:
            if pending_matches_next_bar:
                continue
            if live_bar_id is None:
                continue
            deferred_wait = {
                **htf,
                "allowed": False,
                "reason": "WAIT_MTF_KC_NEXT_BAR_COLOR",
                "side": candidate_side,
                "price": quote_value,
                "open": open_price,
                "ma5": ma5_current,
                "previous_ma5": ma5_previous,
                "ma15": ma15_current,
                "kc_middle_1m": middle,
                "entry_band_ratio": ratio,
                "deferred_candidate": {
                    "side": candidate_side,
                    "source_bar_id": live_bar_id,
                    "state": closed_trend,
                    "reason": "MTF_KC_TREND_ENTRY",
                },
            }
            continue

        if not colorless_reverse:
            if _is_ltf_wave_tail_end(
                closed, candidate_side, quote_value, open_price, upper, lower,
            ):
                return {
                    **htf,
                    "allowed": False,
                    "reason": "BLOCKED_MTF_WAVE_TAIL_END_AFTER_OUTER_BAND",
                    "side": candidate_side,
                }
            giant_blocked, giant_reason = _is_giant_or_exhausted_candle(
                closed, candidate_side, quote_value, open_price,
                live_high, live_low, middle, half_width, ma15_current, previous_atr,
            )
            if giant_blocked:
                return {
                    **htf,
                    "allowed": False,
                    "reason": giant_reason,
                    "side": candidate_side,
                }

        return {
            **htf,
            "allowed": True,
            "reason": (
                "MTF_KC_IMMEDIATE_REVERSE_ENTRY"
                if colorless_reverse
                else "MTF_KC_MIDLINE_HOLD_ENTRY"
                if midline_hold_signal
                else
                "MTF_KC_BREAKOUT_ENTRY"
                if breakout_allowed
                else "MTF_KC_TREND_ENTRY"
            ),
            "side": candidate_side,
            "price": quote_value,
            "open": open_price,
            "ma5": ma5_current,
            "previous_ma5": ma5_previous,
            "ma15": ma15_current,
            "kc_middle_1m": middle,
            "kc_upper_1m": upper,
            "kc_lower_1m": lower,
            "half_width_1m": half_width,
            "entry_band_ratio": ratio,
            "ltf_atr": previous_atr,
            "bandwidth_pct": bandwidth_pct,
            "breakout_edge_offset": breakout_edge_offset,
        }

    if deferred_wait is not None:
        return deferred_wait

    return {
        **htf,
        "allowed": False,
        "reason": "BLOCKED_MTF_KC_ENTRY_CONDITIONS",
    }


def is_mtf_managed_position(
    position: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    return any(
        isinstance(source, Mapping)
        and (
            source.get("mtf_kc_state_machine")
            or source.get("entry_method_authority") == MTF_KC_STATE_MACHINE
        )
        for source in (position, metadata)
    )


def update_mtf_position_protection(
    position: dict[str, Any], metadata: dict[str, Any], price: float,
) -> dict[str, Any] | None:
    """Track MTF position peaks; cycle-end exits and account emergencies own closing."""
    if not is_mtf_managed_position(position, metadata):
        return None
    try:
        quote = float(price)
        entry = float(position.get("entry_price") or 0.0)
        side = str(position.get("side") or "").upper()
    except (TypeError, ValueError, OverflowError):
        return {"reason": "BLOCKED_MTF_KC_PROTECTION_DATA_INVALID"}
    if (
        not all(math.isfinite(value) and value > 0 for value in (quote, entry))
        or side not in ("LONG", "SHORT")
    ):
        return {"reason": "BLOCKED_MTF_KC_PROTECTION_DATA_INVALID"}

    favorable_price = max(entry, quote) if side == "LONG" else min(entry, quote)
    peak = position.get("mtf_peak_favorable_price", metadata.get("mtf_peak_favorable_price"))
    try:
        peak_value = float(peak) if peak is not None else entry
    except (TypeError, ValueError, OverflowError):
        peak_value = entry
    peak_value = max(peak_value, favorable_price) if side == "LONG" else min(peak_value, favorable_price)
    position["mtf_peak_favorable_price"] = peak_value
    metadata["mtf_peak_favorable_price"] = peak_value

    position.pop("mtf_kc_stop_price", None)
    metadata.pop("mtf_kc_stop_price", None)
    return None


def authorize_mtf_exit(
    account: Any,
    symbol: str,
    reason: str,
    price: float,
    evidence: Mapping[str, Any],
    *,
    allow_direction_mismatch: bool = False,
) -> bool:
    if not mtf_exit_reason_allowed(reason):
        return False
    meta = getattr(account, "position_meta", {}).get(symbol)
    if not isinstance(meta, dict):
        return False
    position = getattr(account, "positions", {}).get(symbol, {})
    if not is_mtf_managed_position(position, meta):
        side = str(position.get("side") or "").upper()
        state = str(evidence.get("state") or "").upper()
        expected_state = "UPTREND" if side == "SHORT" else "DOWNTREND"
        expected_reason = (
            f"{MTF_KC_EXIT_REASON_PREFIX}{expected_state}_REVERSAL_EXIT"
        )
        if (
            not allow_direction_mismatch
            or evidence.get("forced_state_mismatch") is not True
            or evidence.get("valid") is not True
            or side not in ("LONG", "SHORT")
            or state != expected_state
            or reason != expected_reason
        ):
            return False
    meta["mtf_kc_exit_authority"] = {
        "reason": reason,
        "side": str(position.get("side") or "").upper(),
        "price": float(price),
        "evidence": dict(evidence),
        "created_at": time.time(),
    }
    save_state = getattr(account, "save_state", None)
    if callable(save_state):
        try:
            save_state()
        except Exception as exc:
            meta.pop("mtf_kc_exit_authority", None)
            logging.getLogger(__name__).error(
                "MTF_EXIT_AUTHORITY_PERSIST_FAILED symbol=%s reason=%s error=%s",
                symbol, reason, exc,
            )
            return False
    return True


def mtf_exit_is_authorized(
    metadata: Mapping[str, Any] | None,
    reason: str,
    price: float,
    side: str | None = None,
) -> bool:
    if not isinstance(metadata, Mapping):
        return False
    ticket = metadata.get("mtf_kc_exit_authority")
    if (
        not isinstance(ticket, Mapping)
        or (
            not mtf_exit_reason_allowed(ticket.get("reason"))
        )
        or ticket.get("reason") != reason
        or (
            side is not None
            and str(ticket.get("side") or "").upper() != str(side).upper()
        )
    ):
        return False
    try:
        ticket_price = float(ticket.get("price"))
        exit_price = float(price)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(ticket_price) and ticket_price > 0
        and math.isfinite(exit_price) and exit_price > 0
        and math.isclose(ticket_price, exit_price, rel_tol=1e-12, abs_tol=0.0)
    )
