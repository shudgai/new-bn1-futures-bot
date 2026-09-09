"""Live outside entries follow closed CK trend; order route checks room and risk."""
import math
from core.channel_pivot_entry import closed_ck_direction

OUTER_CODES = {"KC_OUTSIDE_LONG", "KC_OUTSIDE_SHORT"}


def outside_entry(frame, price):
    wait = {"action": "WAIT", "side": None, "reason": "KC_INSIDE_CHANNEL"}
    try:
        row = frame.iloc[-1]
        lower, upper, price = float(row['kc_lower']), float(row['kc_upper']), float(price)
        if not all(math.isfinite(v) and v > 0 for v in (lower, upper, price)) or lower >= upper:
            return {**wait, "reason": "KC_DATA_INVALID"}
        side = 'LONG' if price > upper else 'SHORT' if price < lower else None
        if side:
            if closed_ck_direction(frame) != side:
                return {**wait, "reason": "KC_DIRECTION_BLOCK_" + side}
            return {"action": "ENTER", "side": side, "reason": "KC_OUTSIDE_" + side}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {**wait, "reason": "KC_DATA_UNAVAILABLE"}
    return wait
