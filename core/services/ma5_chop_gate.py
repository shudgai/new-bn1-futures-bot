"""Single MA5 direction-switch gate using six completed one-minute candles."""
import math

from core.services.candle_data import closed_entry_candles


def ma5_chop_problem(frame):
    try:
        closed = closed_entry_candles(frame).tail(6)
        if len(closed) != 6:
            return "WAIT_MA5_CHOP_DATA"
        stamps = [float(v) for v in closed["timestamp"]]
        values = [float(v) for v in closed["ma5"]]
        if (not all(math.isfinite(v) and v > 0 for v in stamps + values)
                or any(b - a != 60000 for a, b in zip(stamps, stamps[1:]))):
            return "WAIT_MA5_CHOP_DATA"
        previous = 0
        turns = 0
        for before, after in zip(values, values[1:]):
            if math.isclose(before, after, rel_tol=1e-12, abs_tol=0.):
                continue
            direction = 1 if after > before else -1
            turns += bool(previous and direction != previous)
            previous = direction
        return "BLOCKED_MA5_CHOP_TURNS" if turns >= 2 else None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return "WAIT_MA5_CHOP_DATA"
