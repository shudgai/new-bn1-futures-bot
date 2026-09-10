"""Live outside entries use the current CK rail; order route checks room and risk."""
import math

OUTER_CODES = {"KC_OUTSIDE_LONG", "KC_OUTSIDE_SHORT"}
TREND_CODES = {"KC_MIDDLE_TREND_LONG", "KC_MIDDLE_TREND_SHORT"}


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


def next_live_push_entry(frame, price):
    """Enter on the live second candle after a genuine closed CK breakout."""
    wait = {"action": "WAIT", "side": None, "reason": "KC_NEXT_LIVE_PUSH_WAIT"}
    required = {"open", "high", "low", "close", "ma3", "ma15", "kc_upper", "kc_lower"}
    if frame is None or len(frame) < 3 or not required.issubset(frame.columns):
        return {**wait, "reason": "KC_NEXT_LIVE_PUSH_DATA_UNAVAILABLE"}
    try:
        prior, breakout, live = frame.iloc[-3], frame.iloc[-2], frame.iloc[-1]
        values = [
            float(prior[key]) for key in required
        ] + [float(breakout[key]) for key in required] + [
            float(live["open"]), float(live["high"]), float(live["low"]),
            float(live["ma15"]), float(price),
        ]
        if not all(math.isfinite(value) and value > 0 for value in values):
            return {**wait, "reason": "KC_NEXT_LIVE_PUSH_DATA_INVALID"}
        prior_upper, prior_lower = float(prior["kc_upper"]), float(prior["kc_lower"])
        break_upper, break_lower = float(breakout["kc_upper"]), float(breakout["kc_lower"])
        live_upper, live_lower = float(live["kc_upper"]), float(live["kc_lower"])
        break_range = float(breakout["high"]) - float(breakout["low"])
        break_open = float(breakout["open"])
        break_close = float(breakout["close"])
        break_body = abs(break_close - break_open)
        if min(prior_lower, break_lower, live_lower) >= max(prior_upper, break_upper, live_upper):
            return {**wait, "reason": "KC_NEXT_LIVE_PUSH_DATA_INVALID"}
        if break_range <= 0 or break_body / break_range < 0.20:
            return wait
        ma15 = [float(frame["ma15"].iloc[index]) for index in (-4, -3, -2, -1)]
        ma3 = [float(frame["ma3"].iloc[index]) for index in (-3, -2, -1)]
        if not all(math.isfinite(value) and value > 0 for value in ma15):
            return {**wait, "reason": "KC_NEXT_LIVE_PUSH_DATA_INVALID"}
        if not all(math.isfinite(value) and value > 0 for value in ma3):
            return {**wait, "reason": "KC_NEXT_LIVE_PUSH_DATA_INVALID"}
        live_open = float(live["open"])
        live_body_gap = abs(live_open - float(breakout["close"]))
        max_gap = break_body * 0.25
        if live_body_gap > max_gap:
            return wait
        long_break = (
            float(prior["close"]) <= prior_upper
            and break_upper >= prior_upper
            and break_open <= break_upper <= break_open + break_body * 0.5
            and break_close > break_upper
            and float(price) > float(breakout["close"])
            and float(price) > live_open
            and float(price) > live_upper
            and ma3[0] <= ma3[1] < ma3[2]
            and not (ma15[0] > ma15[1] > ma15[2])
            and not (ma15[0] < ma15[1] < ma15[2])
        )
        short_break = (
            float(prior["close"]) >= prior_lower
            and break_lower <= prior_lower
            and break_open >= break_lower >= break_open - break_body * 0.5
            and break_close < break_lower
            and float(price) < float(breakout["close"])
            and float(price) < live_open
            and float(price) < live_lower
            and ma3[0] >= ma3[1] > ma3[2]
            and not (ma15[0] < ma15[1] < ma15[2])
            and not (ma15[0] > ma15[1] > ma15[2])
        )
        if long_break and two_closed_bodies_ready(frame, "LONG"):
            return {"action": "ENTER", "side": "LONG", "reason": "KC_NEXT_LIVE_PUSH_LONG"}
        if short_break and two_closed_bodies_ready(frame, "SHORT"):
            return {"action": "ENTER", "side": "SHORT", "reason": "KC_NEXT_LIVE_PUSH_SHORT"}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {**wait, "reason": "KC_NEXT_LIVE_PUSH_DATA_INVALID"}
    return wait


def continuation_entry(frame, price):
    """Enter a still-running breakout missed on its first successor candle."""
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
            (float(breakout["open"]) <= float(breakout["kc_upper"])
             <= float(breakout["close"]) or float(breakout["close"]) > float(breakout["kc_upper"]))
            and float(confirmation["close"]) > float(confirmation["open"])
            and float(confirmation["close"]) > float(confirmation["kc_upper"])
            and float(price) > float(live["kc_upper"])
            and float(confirmation["kc_upper"]) >= float(breakout["kc_upper"])
            and ma3[0] < ma3[1] <= ma3[2]
        )
        short_signal = (
            (float(breakout["open"]) >= float(breakout["kc_lower"])
             >= float(breakout["close"]) or float(breakout["close"]) < float(breakout["kc_lower"]))
            and float(confirmation["close"]) < float(confirmation["open"])
            and float(confirmation["close"]) < float(confirmation["kc_lower"])
            and float(price) < float(live["kc_lower"])
            and float(confirmation["kc_lower"]) <= float(breakout["kc_lower"])
            and ma3[0] > ma3[1] >= ma3[2]
        )
        if long_signal and two_closed_bodies_ready(frame, "LONG"):
            return {"action": "ENTER", "side": "LONG", "reason": "KC_CONTINUATION_LONG"}
        if short_signal and two_closed_bodies_ready(frame, "SHORT"):
            return {"action": "ENTER", "side": "SHORT", "reason": "KC_CONTINUATION_SHORT"}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {**wait, "reason": "KC_CONTINUATION_DATA_INVALID"}
    return wait


def outside_reentry(frame, price, side):
    """Same-side CK reentry needs a live directional candle and MA3 slope."""
    decision = next_live_push_entry(frame, price)
    if decision.get("side") != side:
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
