"""Shared live MA5 slope qualification for automatic entry routes."""
import math
from typing import Optional

MIN_MA5_SLOPE_ATR = 0.05


def live_ma5_slope_atr(frame, quote):
    """Return the quote-adjusted MA5 slope in ATR units, or None if invalid."""
    try:
        if frame is None or len(frame) < 2:
            return None
        previous, current = frame.iloc[-2], frame.iloc[-1]
        previous_ma5 = float(previous["ma5"])
        current_ma5 = float(current["ma5"])
        close = float(current["close"])
        quote = float(quote)
        atr = float(previous["atr"])
        values = (previous_ma5, current_ma5, close, quote, atr)
        if not all(math.isfinite(value) and value > 0 for value in values):
            return None
        live_ma5 = current_ma5 if bool(current.get("is_closed", False)) else current_ma5 + (quote - close) / 5.0
        slope_atr = (live_ma5 - previous_ma5) / atr
        return slope_atr if math.isfinite(slope_atr) else None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def live_ma5_slope_problem(frame, quote, side):
    """Return a diagnostic block reason unless MA5 slope is strong and aligned."""
    slope = live_ma5_slope_atr(frame, quote)
    if side not in ("LONG", "SHORT") or slope is None:
        return "BLOCKED_BY_MA5_SLOPE_INVALID"
    signed_slope = slope if side == "LONG" else -slope
    if signed_slope <= 0:
        return "BLOCKED_BY_MA5_SLOPE_DOWN" if side == "LONG" else "BLOCKED_BY_MA5_SLOPE_UP"
    if (signed_slope < MIN_MA5_SLOPE_ATR
            and not math.isclose(signed_slope, MIN_MA5_SLOPE_ATR, rel_tol=1e-12)):
        return "BLOCKED_BY_MA5_SLOPE_INSUFFICIENT"
    return None


def live_ma5_entry_direction_problem(frame, quote, side) -> Optional[str]:
    """Require live MA5 slope and quote position to agree with the entry side."""
    try:
        if frame is None or len(frame) < 2 or side not in ("LONG", "SHORT"):
            return "BLOCKED_BY_MA5_SLOPE_INVALID"
        previous, current = frame.iloc[-2], frame.iloc[-1]
        previous_ma5 = float(previous["ma5"])
        current_ma5 = float(current["ma5"])
        close = float(current["close"])
        quote = float(quote)
        values = (previous_ma5, current_ma5, close, quote)
        if not all(math.isfinite(value) and value > 0 for value in values):
            return "BLOCKED_BY_MA5_SLOPE_INVALID"

        live_ma5 = current_ma5 + (quote - close) / 5.0
        if not math.isfinite(live_ma5) or live_ma5 <= 0:
            return "BLOCKED_BY_MA5_SLOPE_INVALID"

        if side == "LONG" and (
            live_ma5 <= previous_ma5 or quote < live_ma5
        ):
            return "BLOCKED_BY_MA5_WEAK_SLOPE"
        if side == "SHORT" and (
            live_ma5 >= previous_ma5 or quote > live_ma5
        ):
            return "BLOCKED_BY_MA5_STRONG_SLOPE"
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "BLOCKED_BY_MA5_SLOPE_INVALID"
