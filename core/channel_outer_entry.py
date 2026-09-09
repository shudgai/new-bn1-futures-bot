"""Live outside entries follow closed CK trend; order route checks room and risk."""
import math
from core.channel_pivot_entry import closed_ck_direction

OUTER_CODES = {"KC_OUTSIDE_LONG", "KC_OUTSIDE_SHORT"}
TREND_CODES = {"KC_MIDDLE_TREND_LONG", "KC_MIDDLE_TREND_SHORT"}


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


def middle_trend_entry(frame, price):
    """A closed middle trend plus live candle color needs no pivot or crossing."""
    wait = {"action": "WAIT", "side": None, "reason": "KC_MIDDLE_TREND_WAIT"}
    side = closed_ck_direction(frame, require_outer_slope=False)
    if side is None:
        return wait
    try:
        row = frame.iloc[-1]
        opened, price = float(row["open"]), float(price)
        lower, upper = float(row["kc_lower"]), float(row["kc_upper"])
        if not all(math.isfinite(v) and v > 0 for v in (opened, price, lower, upper)) or lower >= upper:
            return {**wait, "reason": "KC_DATA_INVALID"}
        if (side == "LONG" and price > opened) or (side == "SHORT" and price < opened):
            return {"action": "ENTER", "side": side, "reason": "KC_MIDDLE_TREND_" + side}
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return {**wait, "reason": "KC_DATA_INVALID"}
    return wait
