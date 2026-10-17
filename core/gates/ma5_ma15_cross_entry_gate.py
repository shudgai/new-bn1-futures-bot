"""Fail-closed completed-bar MA5/MA15 crossover entry authorization."""

import math
from typing import Any, Dict, Tuple

import pandas as pd

from core.services.candle_data import closed_entry_candles


def _kc_direction(row: pd.Series) -> str:
    value = str(row.get("kc_trend") or row.get("channel_state") or "").strip().upper()
    if value in {"UP", "KC向上", "KC_TREND_UP", "UPTREND", "BULLISH", "LONG"}:
        return "UP"
    if value in {"DOWN", "KC向下", "KC_TREND_DOWN", "DOWNTREND", "BEARISH", "SHORT"}:
        return "DOWN"
    return "RANGE"


def validate_ma5_ma15_cross_entry(
    frame: Any,
    side: str,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Require a fresh completed-bar cross, aligned MA5 slope, KC direction and body."""
    side = str(side).upper()
    if side not in {"LONG", "SHORT"}:
        return False, "BLOCKED_INVALID_SIDE", {}
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", {}

    try:
        closed = closed_entry_candles(frame)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", {}
    if len(closed) < 2:
        return False, "BLOCKED_MA_CROSS_INSUFFICIENT_CLOSED_BARS", {}
    previous, current = closed.iloc[-2], closed.iloc[-1]
    try:
        ma5_prev = float(previous.get("ma5", previous.get("_wild_ma5")))
        ma15_prev = float(previous["ma15"])
        ma5_curr = float(current.get("ma5", current.get("_wild_ma5")))
        ma15_curr = float(current["ma15"])
        opening = float(current["open"])
        close = float(current["close"])
        values = (ma5_prev, ma15_prev, ma5_curr, ma15_curr, opening, close)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", {}
    if not all(math.isfinite(value) and value > 0 for value in values):
        return False, "BLOCKED_MA_CROSS_MARKET_DATA_INVALID", {}

    direction = _kc_direction(current)
    explicit_direction = str(
        current.get("kc_trend") or current.get("channel_state") or ""
    ).strip()
    try:
        previous_middle = float(previous["kc_middle"])
        current_middle = float(current["kc_middle"])
        middle_direction = (
            "UP" if current_middle > previous_middle
            else "DOWN" if current_middle < previous_middle
            else "RANGE"
        )
        if explicit_direction:
            if direction != middle_direction:
                return False, "BLOCKED_KC_TREND_DATA_CONFLICT", {}
        else:
            direction = middle_direction
    except (KeyError, TypeError, ValueError, OverflowError):
        if not explicit_direction:
            direction = "RANGE"
    details = {
        "side": side,
        "previous_ma5": ma5_prev,
        "previous_ma15": ma15_prev,
        "current_ma5": ma5_curr,
        "current_ma15": ma15_curr,
        "kc_trend": direction,
        "open": opening,
        "close": close,
        "confirmation_bar_id": current.get("timestamp"),
    }
    if side == "LONG":
        cross = ma5_prev <= ma15_prev and ma5_curr > ma15_curr
        valid = (
            cross and ma5_curr > ma5_prev
            and direction == "UP" and close > opening
        )
        failure_reason = "BLOCKED_MA5_MA15_GOLDEN_CROSS_REQUIRED"
    else:
        cross = ma5_prev >= ma15_prev and ma5_curr < ma15_curr
        valid = (
            cross and ma5_curr < ma5_prev
            and direction == "DOWN" and close < opening
        )
        failure_reason = "BLOCKED_MA5_MA15_DEATH_CROSS_REQUIRED"
    if not valid:
        return False, failure_reason, details

    try:
        from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

        row = current
        previous_middle = float(previous["kc_middle"])
        current_middle = float(current["kc_middle"])
        indicators = {
            "kc_upper": float(row["kc_upper"]),
            "kc_lower": float(row["kc_lower"]),
            "kc_middle": current_middle,
            "ma15": ma15_curr,
            "atr": float(row["atr"]),
            "ma5_current": ma5_curr,
            "ma5_previous": ma5_prev,
            "kc_middle_current": current_middle,
            "kc_middle_previous": previous_middle,
        }
        macro_allowed, macro_reason = MacroCandleStructureGate(
            max_bias_atr=1.2,
        ).can_open(
            side,
            {
                name: float(row[name])
                for name in ("open", "high", "low", "close")
            },
            indicators,
            symbol="LOBSTER/USDT",
            entry_pattern="CROSSOVER",
            structure_confirmation="MA5_MA15_1M_CROSS",
            structure_confirmed=True,
        )
        kc_upper = indicators["kc_upper"]
        kc_lower = indicators["kc_lower"]
        atr = indicators["atr"]
        close_price = float(row["close"])
        body = abs(close_price - float(row["open"]))
        upper_wick = float(row["high"]) - max(float(row["open"]), close_price)
        lower_wick = min(float(row["open"]), close_price) - float(row["low"])
        channel_width = kc_upper - kc_lower
        if (
            not macro_allowed
            or channel_width <= 0
            or atr <= 0
        ):
            return False, macro_reason, details
        kc_position = (close_price - kc_lower) / channel_width
        bias_atr = abs(close_price - ma15_curr) / atr
        if side == "LONG" and kc_position > 0.35:
            return False, "VETO_MACRO_KC_POSITION_LONG", details
        if side == "SHORT" and kc_position < 0.65:
            return False, "VETO_MACRO_KC_POSITION_SHORT", details
        if bias_atr > 1.2:
            return False, "VETO_MACRO_MA15_BIAS", details
        if side == "LONG" and upper_wick > body and close_price < float(row["high"]):
            return False, "VETO_MACRO_LONG_UPPER_WICK_EXHAUSTION", details
        if side == "SHORT" and lower_wick > body and close_price > float(row["low"]):
            return False, "VETO_MACRO_SHORT_LOWER_WICK_EXHAUSTION", details
    except (KeyError, TypeError, ValueError, OverflowError):
        return False, "BLOCKED_MACRO_STRUCTURE_DATA_INVALID", details

    return (
        True,
        "PERMIT_MA5_MA15_GOLDEN_CROSS"
        if side == "LONG" else "PERMIT_MA5_MA15_DEATH_CROSS",
        details,
    )
