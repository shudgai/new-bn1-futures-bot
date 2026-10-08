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


def ma5_ma15_entanglement_problem(frame, quote, side):
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 3 or side not in ("LONG", "SHORT"):
            return "WAIT_MA5_MA15_DATA"
        recent = closed.tail(3)
        if len(frame) != len(closed) + 1:
            return "WAIT_MA5_MA15_DATA"
        values = [[float(row[k]) for k in ("ma5", "ma15", "atr")]
                  for _, row in recent.iterrows()]
        if any(not math.isfinite(v) or v <= 0 for row in values for v in row):
            return "WAIT_MA5_MA15_DATA"
        gaps = [(abs(a-b), .10*atr) for a, b, atr in values]
        if not all(gap <= limit or math.isclose(gap, limit, rel_tol=1e-12)
                   for gap, limit in gaps):
            return None
        if len(closed) < 14:
            return "WAIT_MA5_MA15_DATA"
        price = float(quote)
        prices = [float(v) for v in closed.close.tail(14)] + [price]
        if not all(math.isfinite(v) and v > 0 for v in prices):
            return "WAIT_MA5_MA15_DATA"
        live5, live15 = sum(prices[-5:])/5., sum(prices)/15.
        previous5, previous15, atr = values[-1]
        sign = 1 if side == "LONG" else -1
        gap = sign*(live5-live15)
        limit = .10*atr
        if (gap > limit and not math.isclose(gap, limit, rel_tol=1e-12)
                and sign*(live5-previous5) > max(live5, previous5)*1e-12
                and sign*(live15-previous15) > max(live15, previous15)*1e-12):
            return None
        return "BLOCKED_MA5_MA15_ENTANGLED"
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return "WAIT_MA5_MA15_DATA"
