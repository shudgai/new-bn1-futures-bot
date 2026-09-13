"""Read-only compatibility helper for retired surge-recovery diagnostics.

The production entry path must not import this module.  It only preserves the
old diagnostic contract used by historical regression tests and tools.
"""

import math


def _effective_bullish_closed_bar(row) -> bool:
    try:
        opened = float(row["open"])
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        atr = float(row.get("atr", 0.0))
        body = close - opened
        candle_range = high - low
        if not all(math.isfinite(value) for value in (opened, close, high, low, atr)):
            return False
        if body <= 0 or candle_range <= 0:
            return False
        return body >= max(0.2 * atr, 0.2 * candle_range)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _is_upward_surge(row) -> bool:
    try:
        opened = float(row["open"])
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        atr = float(row.get("atr", 0.0))
        return (
            all(math.isfinite(value) for value in (opened, close, high, low, atr))
            and close > opened
            and high > low
            and high - low >= 1.6 * atr
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def surge_recovery_entry(frame, price):
    """Report unresolved historical surge state without gating production entries."""
    del price
    if frame is None or len(frame) < 3:
        return {"action": "WAIT", "reason": "SURGE_DATA_WAIT"}
    try:
        # The final row is the still-forming bar and cannot release a surge.
        closed = frame.iloc[:-1]
        if closed.empty:
            return {"action": "WAIT", "reason": "SURGE_CLOSED_BAR_WAIT"}
        surge_positions = [index for index, row in frame.iterrows() if _is_upward_surge(row)]
        if not surge_positions:
            return None
        latest_surge = surge_positions[-1]
        latest_surge_position = frame.index.get_loc(latest_surge)
        if latest_surge_position == len(frame) - 1:
            return {"action": "WAIT", "reason": "SURGE_LIVE_WAIT"}
        recovery = closed.iloc[latest_surge_position + 1:]
        if recovery.empty:
            return {"action": "WAIT", "reason": "SURGE_RECOVERY_WAIT"}
        if _effective_bullish_closed_bar(recovery.iloc[-1]):
            return None
        return {"action": "WAIT", "reason": "SURGE_RECOVERY_WAIT"}
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return {"action": "WAIT", "reason": "SURGE_DATA_WAIT"}


__all__ = ["surge_recovery_entry"]