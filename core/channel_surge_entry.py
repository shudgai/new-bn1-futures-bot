"""Long entries after an abnormal upward candle wait for a closed local trough."""
import math
from core.channel_pivot_entry import pivot_entry


def surge_recovery_entry(frame, price):
    """None means no recent surge; otherwise return the recovery decision.

    Examine the same last 60 completed bars regardless of fetch size. A live
    upward surge blocks chasing too, but cannot confirm a price trough.
    """
    wait = {"action": "WAIT", "side": None, "reason": "KC_SURGE_WAIT_TROUGH"}
    required = {"open", "high", "low", "close", "kc_upper", "kc_lower"}
    if frame is None or len(frame) < 4 or not required.issubset(frame.columns):
        return None
    try:
        price = float(price)
        if not math.isfinite(price) or price <= 0:
            return wait
        recent = frame.iloc[-70:]
        latest_surge = None
        for i in range(max(0, len(recent) - 61), len(recent)):
            row = recent.iloc[i]
            opened, high, low, closed, upper, lower = (float(row[k]) for k in
                ("open", "high", "low", "close", "kc_upper", "kc_lower"))
            if not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed, upper, lower)):
                return wait
            if not low <= min(opened, closed) <= max(opened, closed) <= high or lower >= upper:
                return wait
            if i == len(recent) - 1:
                closed, high, low = price, max(high, price), min(low, price)
            body = closed - opened
            if body <= 0:
                continue
            prior = recent.iloc[max(0, i - 9):i]
            average = (prior["close"].astype(float) - prior["open"].astype(float)).abs().mean()
            abnormal = (body >= .8 * (upper - lower) or high - low > 1.25 * (upper - lower)
                        or (len(prior) == 9 and math.isfinite(average) and average > 0 and body >= 3 * average))
            if abnormal:
                latest_surge = i
            elif i < len(recent) - 1 and high > low and body / (high - low) >= .20:
                # A later closed effective green candle supersedes the old surge.
                latest_surge = None
        if latest_surge is None:
            return None
        # Trough must form after the surge, not be a pre-surge buy signal.
        if latest_surge >= len(recent) - 3:
            return wait
        decision = pivot_entry(frame, price)
        if decision.get("side") != "LONG":
            return wait
        left, trough, confirmation, live = (frame.iloc[i] for i in (-4, -3, -2, -1))
        span = float(confirmation["high"]) - float(confirmation["low"])
        body = float(confirmation["close"]) - float(confirmation["open"])
        if span <= 0 or body / span < .20:
            return wait
        # Require an actual retreat from the surge high, and reject a fresh chase
        # or a trough already broken by the forming candle.
        if (float(trough["low"]) >= float(recent.iloc[latest_surge]["high"])
                or float(live["low"]) <= float(trough["low"])
                or price > float(confirmation["high"])):
            return {**wait, "reason": "KC_SURGE_TROUGH_PRICE_INVALID"}
        return decision
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return wait


def long_entry_recovery_ready(frame, price, side, ordinary_ready):
    """All long order routes share the same surge veto and trough exception."""
    recovery = surge_recovery_entry(frame, price) if side == "LONG" else None
    return recovery.get("side") == side if recovery is not None else ordinary_ready(frame, price, side)
