"""Live outside entries use the current CK rail; order route checks room and risk."""
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

        # A single fully closed directional candle outside CK is enough to enter;
        # do not require a second confirmation candle if price has only retraced
        # inside the channel by the next scan.
        if len(frame) >= 2:
            closed = frame.iloc[-2]
            closed_open = float(closed["open"])
            closed_close = float(closed["close"])
            closed_upper = float(closed["kc_upper"])
            closed_lower = float(closed["kc_lower"])
            values = (closed_open, closed_close, closed_upper, closed_lower)
            if all(math.isfinite(v) and v > 0 for v in values) and closed_lower < closed_upper:
                if closed_close < closed_open and closed_close < closed_lower:
                    if closed_ck_direction(frame) != "SHORT":
                        return {**wait, "reason": "KC_DIRECTION_BLOCK_SHORT"}
                    return {"action": "ENTER", "side": "SHORT", "reason": "KC_CLOSED_OUTSIDE_SHORT"}
                if closed_close > closed_open and closed_close > closed_upper:
                    if closed_ck_direction(frame) != "LONG":
                        return {**wait, "reason": "KC_DIRECTION_BLOCK_LONG"}
                    return {"action": "ENTER", "side": "LONG", "reason": "KC_CLOSED_OUTSIDE_LONG"}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {**wait, "reason": "KC_DATA_UNAVAILABLE"}
    return wait


def outside_reentry(frame, price, side):
    """Same-side CK reentry needs a live directional candle and MA3 slope."""
    decision = outside_entry(frame, price)
    if decision.get("side") != side:
        return {"action": "WAIT", "side": None, "reason": "KC_REENTRY_WAIT"}
    try:
        sign = 1 if side == "LONG" else -1
        opened = float(frame.iloc[-1]["open"])
        closes = [float(v) for v in frame["close"].iloc[-4:-1]]
        if len(closes) != 3 or not all(math.isfinite(v) and v > 0 for v in [opened, price, *closes]):
            raise ValueError("invalid MA3 data")
        last = sum(closes) / 3
        live = (sum(closes[-2:]) + price) / 3
        if sign * (price - opened) > 0 and sign * (live - last) > 0:
            return decision
    except (TypeError, ValueError, KeyError, IndexError):
        pass
    return {"action": "WAIT", "side": None, "reason": "KC_REENTRY_COLOR_MA3_WAIT"}


def abnormal_pullback_ready(ticket, frame, price):
    """After a close, observe a later candle inside CK before a fresh reclaim."""
    try:
        row = frame.iloc[-1]
        bar = float(row.get("timestamp", row.name))
        exited = float(ticket["exit_bar_id"])
        upper, lower = float(row["kc_upper"]), float(row["kc_lower"])
        if (not all(math.isfinite(v) for v in (bar, exited, upper, lower, price))
                or not 0 < lower < upper or price <= 0 or bar <= exited):
            return False
        if lower <= price <= upper:
            ticket["pullback_bar"] = bar
            return False
        pulled = float(ticket.get("pullback_bar", float("nan")))
        return math.isfinite(pulled) and exited < pulled <= bar
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return False
