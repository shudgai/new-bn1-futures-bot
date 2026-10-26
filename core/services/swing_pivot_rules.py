"""Closed-candle swing-pivot entry and exhaustion-exit evidence."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from core.services.candle_data import closed_entry_candles


def _num(row: Any, key: str) -> float:
    value = float(row.get(key))
    if not math.isfinite(value):
        raise ValueError(key)
    return value


def evaluate_swing_pivot_entry(frame: Any) -> dict[str, Any] | None:
    """Authorize entry only after a closed three-bar peak/valley confirmation."""
    if not isinstance(frame, pd.DataFrame):
        return None
    try:
        if float(frame.attrs.get("timeframe_ms", 0) or 0) != 60_000.0:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    closed = closed_entry_candles(frame)
    if len(closed) < 3:
        return None
    left, pivot, current = closed.iloc[-3], closed.iloc[-2], closed.iloc[-1]
    try:
        close, opening = _num(current, "close"), _num(current, "open")
        high_left, low_left = _num(left, "high"), _num(left, "low")
        high_pivot, low_pivot = _num(pivot, "high"), _num(pivot, "low")
        high_confirm, low_confirm = _num(current, "high"), _num(current, "low")
        pivot_open, pivot_close = _num(pivot, "open"), _num(pivot, "close")
        atr, pivot_atr = _num(current, "atr"), _num(pivot, "atr")
        if min(close, opening, high_left, low_left, high_pivot, low_pivot,
               high_confirm, low_confirm, pivot_open, pivot_close, atr, pivot_atr) <= 0:
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    pivot_mid = (pivot_open + pivot_close) / 2.0
    body = abs(close - opening)
    if (low_pivot < low_left and low_pivot < low_confirm
            and close > opening and close > pivot_mid
            and body >= 0.35 * atr
            and close - low_pivot >= 0.45 * pivot_atr):
        return {
            "side": "LONG", "entry_type": "SWING_PIVOT",
            "stop_loss_price": low_pivot, "atr": atr,
            "bar_timestamp": float(current.get("timestamp", 0.0)),
            "pivot_price": low_pivot,
            "pivot_bar_timestamp": float(pivot.get("timestamp", 0.0)),
            "confirmation_bar_timestamp": float(current.get("timestamp", 0.0)),
        }
    if (high_pivot > high_left and high_pivot > high_confirm
            and close < opening and close < pivot_mid
            and body >= 0.35 * atr
            and high_pivot - close >= 0.45 * pivot_atr):
        return {
            "side": "SHORT", "entry_type": "SWING_PIVOT",
            "stop_loss_price": high_pivot, "atr": atr,
            "bar_timestamp": float(current.get("timestamp", 0.0)),
            "pivot_price": high_pivot,
            "pivot_bar_timestamp": float(pivot.get("timestamp", 0.0)),
            "confirmation_bar_timestamp": float(current.get("timestamp", 0.0)),
        }
    return None


def evaluate_outer_rail_reversal_exit(position: Any, frame: Any) -> dict[str, Any] | None:
    """Return closed-bar peak/valley reversal evidence after outer-rail contact."""
    if not isinstance(frame, pd.DataFrame):
        return None
    try:
        if float(frame.attrs.get("timeframe_ms", 0) or 0) != 60_000.0:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    side = str((position or {}).get("side") or "").upper()
    if side not in ("LONG", "SHORT"):
        return None
    bars = closed_entry_candles(frame)
    if len(bars) < 2:
        return None
    try:
        opened = float((position or {}).get("open_timestamp") or (position or {}).get("entry_timestamp") or 0.0)
        opened_ms = opened * 1000 if 0 < opened < 1e11 else opened
        entry_bar = math.floor(opened_ms / 60_000) * 60_000 if opened_ms > 0 else 0
        current, previous = bars.iloc[-1], bars.iloc[-2]
        stamp = _num(current, "timestamp")
        if stamp <= entry_bar:
            return None
        c_open, c_close = _num(current, "open"), _num(current, "close")
        p_open, p_close = _num(previous, "open"), _num(previous, "close")
        high, low = _num(current, "high"), _num(current, "low")
        upper, lower = _num(current, "kc_upper"), _num(current, "kc_lower")
        prev_upper, prev_lower = _num(previous, "kc_upper"), _num(previous, "kc_lower")
        ma3, atr = _num(current, "ma3"), _num(current, "atr")
        if atr <= 0 or min(upper, lower, prev_upper, prev_lower, ma3) <= 0:
            return None
        body = abs(c_close - c_open)
        prev_body = abs(p_close - p_open)
        upper_wick = high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - low
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    if side == "LONG":
        touched = bool((bars.loc[bars["timestamp"].astype(float) > entry_bar, "high"].astype(float)
                        >= bars.loc[bars["timestamp"].astype(float) > entry_bar, "kc_upper"].astype(float)).any())
        engulf = (
            p_close > p_open and p_close > prev_upper and c_close < c_open
            and prev_body > 0 and c_close <= p_close - 0.6 * prev_body
        )
        wick = c_close < c_open and upper_wick >= 1.2 * max(body, 1e-12)
        fast_break = c_close < ma3 and c_close < c_open and body >= 0.5 * atr
        label = "EXIT_LONG_AT_PEAK"
    else:
        eligible = bars.loc[bars["timestamp"].astype(float) > entry_bar]
        touched = bool((eligible["low"].astype(float) <= eligible["kc_lower"].astype(float)).any())
        engulf = (
            p_close < p_open and p_close < prev_lower and c_close > c_open
            and prev_body > 0 and c_close >= p_close + 0.6 * prev_body
        )
        wick = c_close > c_open and lower_wick >= 1.2 * max(body, 1e-12)
        fast_break = c_close > ma3 and c_close > c_open and body >= 0.5 * atr
        label = "EXIT_SHORT_AT_BOTTOM"
    reason = "OUTER_RAIL_ENGULFING" if engulf else "OUTER_RAIL_LONG_WICK" if wick else "OUTER_RAIL_MA3_BODY_BREAK" if fast_break else None
    if not touched or reason is None:
        return None
    return {
        "reason": label, "trigger": reason, "side": side,
        "bar_timestamp": stamp, "body": body, "previous_body": prev_body,
        "atr": atr, "upper_wick": upper_wick, "lower_wick": lower_wick,
        "kc_rail_touched": True,
    }
