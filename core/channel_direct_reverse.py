"""Matched-fill authorization for direct reverse entries, never manual/stops."""
import math
from core.channel_outer_entry import live_adverse_entry_safe


def authorized(account, symbol, signal, now):
    """Direct-reverse tickets cannot bypass the restored breakout-only policy."""
    return False


def quote_ready(engine, symbol, frame, price, side):
    import time
    try:
        now = time.time()
        quoted = float(getattr(engine, '_channel_entry_quote_times', {}).get(symbol, float('nan')))
        return (math.isfinite(quoted) and 0 <= now - quoted <= 5
                and frame is not None and len(frame) >= 3
                and int(float(frame.iloc[-1]['timestamp']) / 60000) == int(now / 60)
                and math.isfinite(float(price)) and price > 0
                and live_adverse_entry_safe(frame, price, side))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
