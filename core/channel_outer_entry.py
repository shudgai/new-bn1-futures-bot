"""Live outside entries use the current CK rail; order route checks room and risk."""
import math
from core.channel_pivot_entry import closed_ck_direction
from core.channel_surge_entry import surge_recovery_entry

OUTER_CODES = {"KC_OUTSIDE_LONG", "KC_OUTSIDE_SHORT"}
TREND_CODES = {"KC_MIDDLE_TREND_LONG", "KC_MIDDLE_TREND_SHORT"}


def aligned_direction(frame, side):
    """Two closed MA slopes and their latest ordering must agree with closed KC."""
    if side not in ("LONG", "SHORT") or closed_ck_direction(frame) != side:
        return False
    try:
        sign = 1 if side == "LONG" else -1
        previous, latest = frame.iloc[-3], frame.iloc[-2]
        values = [float(row[key]) for row in (previous, latest) for key in ("ma3", "ma15")]
        if not all(math.isfinite(v) and v > 0 for v in values):
            return False
        a, b, c, d = values
        return sign * (c - a) > 0 and sign * (d - b) > 0 and sign * (c - d) > 0
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def aligned_entry(frame, price):
    """Two closed bodies outside the rail allow breakout or continuation entry."""
    wait = {"action": "WAIT", "side": None, "reason": "KC_MA_ALIGNMENT_WAIT"}
    try:
        price = float(price)
        live = frame.iloc[-1]
        lower, upper = float(live["kc_lower"]), float(live["kc_upper"])
        if not all(math.isfinite(v) for v in (price, lower, upper)) or not (price > 0 and 0 < lower < upper):
            return wait
        for _, row in frame.iloc[-4:].iterrows():
            opened, high, low, closed = (float(row[k]) for k in ("open", "high", "low", "close"))
            if (not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed))
                    or not low <= min(opened, closed) <= max(opened, closed) <= high):
                return wait
        side = closed_ck_direction(frame)
        if not aligned_direction(frame, side):
            return wait
        if not two_closed_bodies_ready(frame, side):
            return {**wait, "reason": "KC_TWO_CLOSED_BODIES_WAIT"}
        if not live_ma3_direction_ready(frame, price, side):
            return {**wait, "reason": "KC_LIVE_MA3_DIRECTION_WAIT"}
        if side == "LONG":
            recovery = surge_recovery_entry(frame, price)
            if recovery is not None and recovery.get("action") != "ENTER":
                return recovery
        if confirmed_outer_continuation_ready(frame, price, side):
            return {"action": "ENTER", "side": side, "reason": "KC_CONTINUATION_" + side}
        return {**wait, "reason": "KC_OUTSIDE_WAIT_NEXT_CANDLE"}
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return wait


def aligned_entry_ready(frame, price, side):
    if side not in ("LONG", "SHORT") or aligned_entry(frame, price).get("side") != side:
        return False
    try:
        previous, latest, live = frame.iloc[-3], frame.iloc[-2], frame.iloc[-1]
        ma3_values = [float(previous["ma3"]), float(latest["ma3"])]
        opened, quoted = float(live["open"]), float(price)
        values = (*ma3_values, opened, quoted)
        if not all(math.isfinite(value) and value > 0 for value in values):
            return False
        sign = 1 if side == "LONG" else -1
        ma3_descends_or_rises = all(
            sign * (current - prior) > 0
            for prior, current in zip(ma3_values, ma3_values[1:])
        )
        return ma3_descends_or_rises and live_ma3_direction_ready(frame, price, side)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def live_ma3_direction_ready(frame, price, side):
    """Compare quote-derived live MA3 with closed MA3, ignoring stale live rows."""
    try:
        closes = [float(v) for v in frame["close"].iloc[-4:-1]]
        price = float(price)
        if (side not in ("LONG", "SHORT") or len(closes) != 3
                or not all(math.isfinite(v) and v > 0 for v in [price, *closes])):
            return False
        # Shared closes cancel; avoid rounding a flat MA into a slope.
        return (1 if side == "LONG" else -1) * (price - closes[0]) > 0
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def two_closed_bodies_ready(frame, side):
    """Require two completed directional bodies; the live candle never counts."""
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 3:
            return False
        sign = 1 if side == "LONG" else -1
        for _, row in frame.iloc[-3:-1].iterrows():
            opened, high, low, closed = (float(row[k]) for k in ("open", "high", "low", "close"))
            if not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed)):
                return False
            if not low <= min(opened, closed) <= max(opened, closed) <= high or high <= low:
                return False
            if sign * (closed - opened) <= 0 or abs(closed - opened) / (high - low) < .20:
                return False
        return True
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def three_closed_short_breakout_ready(frame, price):
    """Allow a small red middle candle between solid closed breakout/confirmation."""
    try:
        if frame is None or len(frame) < 4:
            return False
        rows = [frame.iloc[i] for i in (-4, -3, -2)]
        for index, row in enumerate(rows):
            opened, high, low, closed = (float(row[k]) for k in ("open", "high", "low", "close"))
            lower, upper = float(row["kc_lower"]), float(row["kc_upper"])
            if not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed, lower, upper)):
                return False
            if not (low <= closed < opened <= high and lower < upper and closed < lower):
                return False
            if index != 1 and (opened - closed) / (high - low) < .20:
                return False
        first = rows[0]
        live = frame.iloc[-1]
        lower, upper, price = float(live["kc_lower"]), float(live["kc_upper"]), float(price)
        return (float(first["open"]) >= float(first["kc_lower"])
                and all(math.isfinite(v) for v in (lower, upper, price))
                and 0 < price < lower < upper)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def confirmed_outer_continuation_ready(frame, price, side):
    """A missed breakout may continue; both closed bodies must finish outside."""
    if not two_closed_bodies_ready(frame, side):
        return False
    try:
        sign = 1 if side == "LONG" else -1
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        price = float(price)
        if not math.isfinite(price) or price <= 0:
            return False
        for offset in (-3, -2, -1):
            row = frame.iloc[offset]
            lower, upper = float(row["kc_lower"]), float(row["kc_upper"])
            if not all(math.isfinite(v) for v in (lower, upper)) or not 0 < lower < upper:
                return False
            quoted = price if offset == -1 else float(row["close"])
            if sign * (quoted - float(row[rail])) <= 0:
                return False
        return True
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return False


def confirmed_outer_breakout_ready(frame, price, side, allow_three_short=False):
    """Require a closed breakout/confirmation, with the three-red short option."""
    if allow_three_short and side == "SHORT" and three_closed_short_breakout_ready(frame, price):
        return True
    if not two_closed_bodies_ready(frame, side):
        return False
    try:
        sign = 1 if side == "LONG" else -1
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        breakout, confirmation, live = (frame.iloc[i] for i in (-3, -2, -1))
        for row in (breakout, confirmation, live):
            lower, upper = float(row["kc_lower"]), float(row["kc_upper"])
            if not all(math.isfinite(v) for v in (lower, upper)) or not 0 < lower < upper:
                return False
        price = float(price)
        return (math.isfinite(price) and price > 0
                and sign * (float(breakout["open"]) - float(breakout[rail])) <= 0
                and sign * (float(breakout["close"]) - float(breakout[rail])) > 0
                and sign * (float(confirmation["close"]) - float(confirmation[rail])) > 0
                and sign * (price - float(live[rail])) > 0)
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return False


def outside_entry(frame, price):
    wait = {"action": "WAIT", "side": None, "reason": "KC_INSIDE_CHANNEL"}
    try:
        row = frame.iloc[-1]
        lower, upper, price = float(row['kc_lower']), float(row['kc_upper']), float(price)
        if not all(math.isfinite(v) and v > 0 for v in (lower, upper, price)) or lower >= upper:
            return {**wait, "reason": "KC_DATA_INVALID"}
        side = 'LONG' if price > upper else 'SHORT' if price < lower else None
        if side:
            return {**wait, "reason": "KC_OUTSIDE_WAIT_NEXT_CANDLE"}

    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {**wait, "reason": "KC_DATA_UNAVAILABLE"}
    return wait


def continuation_entry(frame, price):
    """Enter after closed breakout confirmation; shorts may bridge one small red."""
    wait = {"action": "WAIT", "side": None, "reason": "KC_CONTINUATION_WAIT"}
    required = {"open", "high", "low", "close", "ma3", "ma15", "kc_upper", "kc_lower"}
    if frame is None or len(frame) < 5 or not required.issubset(frame.columns):
        return {**wait, "reason": "KC_CONTINUATION_DATA_UNAVAILABLE"}
    try:
        breakout, confirmation, live = frame.iloc[-3], frame.iloc[-2], frame.iloc[-1]
        values = [float(row[key]) for row in (breakout, confirmation, live) for key in required]
        values.append(float(price))
        if not all(math.isfinite(value) and value > 0 for value in values):
            return {**wait, "reason": "KC_CONTINUATION_DATA_INVALID"}
        body = abs(float(confirmation["close"]) - float(confirmation["open"]))
        candle_range = float(confirmation["high"]) - float(confirmation["low"])
        if candle_range <= 0 or body / candle_range < 0.20:
            return wait
        ma3 = [float(row["ma3"]) for row in (breakout, confirmation, live)]
        if not all(math.isfinite(value) and value > 0 for value in ma3):
            return {**wait, "reason": "KC_CONTINUATION_DATA_INVALID"}
        long_signal = (
            float(breakout["open"]) <= float(breakout["kc_upper"]) < float(breakout["close"])
            and float(confirmation["close"]) > float(confirmation["open"])
            and float(confirmation["close"]) > float(confirmation["kc_upper"])
            and float(price) > float(live["kc_upper"])
            and float(confirmation["kc_upper"]) >= float(breakout["kc_upper"])
            and ma3[0] < ma3[1] < ma3[2]
        )
        short_signal = (
            (float(breakout["open"]) >= float(breakout["kc_lower"]) > float(breakout["close"])
             or three_closed_short_breakout_ready(frame, price))
            and float(confirmation["close"]) < float(confirmation["open"])
            and float(confirmation["close"]) < float(confirmation["kc_lower"])
            and float(price) < float(live["kc_lower"])
            and float(confirmation["kc_lower"]) <= float(breakout["kc_lower"])
            and ma3[0] > ma3[1] > ma3[2]
        )
        if long_signal and aligned_direction(frame, "LONG") and confirmed_outer_breakout_ready(frame, price, "LONG"):
            return {"action": "ENTER", "side": "LONG", "reason": "KC_CONTINUATION_LONG"}
        if short_signal and aligned_direction(frame, "SHORT") and confirmed_outer_breakout_ready(frame, price, "SHORT"):
            return {"action": "ENTER", "side": "SHORT", "reason": "KC_CONTINUATION_SHORT"}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {**wait, "reason": "KC_CONTINUATION_DATA_INVALID"}
    return wait


def outside_reentry(frame, price, side):
    """Revalidate two closed outside bodies and live MA3 for every reentry."""
    decision = aligned_entry(frame, price)
    if side not in ("LONG", "SHORT") or decision.get("side") != side:
        return {"action": "WAIT", "side": None, "reason": "KC_REENTRY_WAIT"}
    if decision.get("reason") not in TREND_CODES and not decision.get("reason", "").startswith("KC_CONTINUATION_"):
        return decision
    try:
        sign = 1 if side == "LONG" else -1
        price = float(price)
        upper, lower = float(frame.iloc[-1]["kc_upper"]), float(frame.iloc[-1]["kc_lower"])
        if not all(math.isfinite(v) for v in (upper, lower)) or not 0 < lower < upper:
            raise ValueError("invalid CK data")
        opened = float(frame.iloc[-1]["open"])
        closes = [float(v) for v in frame["close"].iloc[-4:-1]]
        if len(closes) != 3 or not all(math.isfinite(v) and v > 0 for v in [opened, price, *closes]):
            raise ValueError("invalid MA3 data")
        last = sum(closes) / 3
        live = (sum(closes[-2:]) + price) / 3
        if sign * (live - last) > 0:
            return decision
    except (TypeError, ValueError, KeyError, IndexError):
        pass
    return {"action": "WAIT", "side": None, "reason": "KC_REENTRY_MA3_WAIT"}


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
