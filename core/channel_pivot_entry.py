"""Closed price pivots aligned with MA15, and their middle-exit state."""
import math


PIVOT_CODES = {"KC_MA15_TROUGH_LONG", "KC_MA15_PEAK_SHORT"}


def pivot_entry(frame, price):
    wait = {"action": "WAIT", "side": None, "reason": "WAIT_MA15_PRICE_PIVOT"}
    required = {"open", "high", "low", "close", "ma3", "ma15", "kc_upper", "kc_lower"}
    if frame is None or len(frame) < 4 or not required.issubset(frame.columns):
        return {**wait, "reason": "KC_DATA_UNAVAILABLE"}
    try:
        left, pivot, right = frame.iloc[-4], frame.iloc[-3], frame.iloc[-2]
        rows = (left, pivot, right)
        values = [float(row[key]) for row in rows for key in required] + [float(price)]
        if not all(math.isfinite(v) and v > 0 for v in values):
            return {**wait, "reason": "KC_DATA_INVALID"}
        if any(not (float(r["low"]) <= min(float(r["open"]), float(r["close"]))
                    <= max(float(r["open"]), float(r["close"])) <= float(r["high"])) for r in rows):
            return {**wait, "reason": "KC_DATA_INVALID"}
        rails = [(float(r["kc_lower"]), float(r["kc_upper"])) for r in (*rows, frame.iloc[-1])]
        if any(not (math.isfinite(lo) and math.isfinite(hi) and 0 < lo < hi) for lo, hi in rails):
            return {**wait, "reason": "KC_DATA_INVALID"}
        rising = float(left["ma15"]) < float(pivot["ma15"]) < float(right["ma15"])
        falling = float(left["ma15"]) > float(pivot["ma15"]) > float(right["ma15"])
        if (rising and float(pivot["low"]) < min(float(left["low"]), float(right["low"]))
                and float(right["close"]) > float(right["open"])
                and float(left["ma3"]) > float(pivot["ma3"]) < float(right["ma3"])
                and price > float(pivot["low"])):
            return {"action": "ENTER", "side": "LONG", "reason": "KC_MA15_TROUGH_LONG"}
        if (falling and float(pivot["high"]) > max(float(left["high"]), float(right["high"]))
                and float(right["close"]) < float(right["open"])
                and float(left["ma3"]) < float(pivot["ma3"]) > float(right["ma3"])
                and price < float(pivot["high"])):
            return {"action": "ENTER", "side": "SHORT", "reason": "KC_MA15_PEAK_SHORT"}
    except (TypeError, ValueError, KeyError, IndexError):
        return {**wait, "reason": "KC_DATA_INVALID"}
    return wait


def pivot_middle_exit(position, price, middle):
    """Persist favorable crossing and latch a failed close for the next scan."""
    if not position.get("channel_pivot_entry"):
        return True  # Existing positions retain their ordinary middle exit.
    if position.get("channel_pivot_middle_exit_pending"):
        return True
    side = position.get("side")
    if side not in ("LONG", "SHORT"):
        return False
    direction = 1 if side == "LONG" else -1
    try:
        if not all(math.isfinite(float(v)) and float(v) > 0 for v in (price, middle)):
            return False
        entry = float(position.get("entry_price") or 0)
        entry_middle = float(position.get("entry_kc_middle") or 0)
        if (entry > 0 and entry_middle > 0 and math.isfinite(entry) and math.isfinite(entry_middle)
                and direction * (entry - entry_middle) > 0):
            position["channel_pivot_middle_reached"] = True
        if direction * (price - middle) > 0:
            position["channel_pivot_middle_reached"] = True
        if position.get("channel_pivot_middle_reached") and direction * (price - middle) <= 0:
            position["channel_pivot_middle_exit_pending"] = True
            return True
    except (TypeError, ValueError):
        return False
    return False
