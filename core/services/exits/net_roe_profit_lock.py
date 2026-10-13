"""Shared net-ROE ladder used by scan-time and final-exit validation."""
import math


NET_ROE_LOCK_START_PCT = 10.0
NET_ROE_LOCK_STEP_PCT = 1.0
NET_ROE_LOCK_GIVEBACK_PCT = 2.0


def tiered_net_roe_floor(peak_net_roe_pct):
    """Return (locked floor, tier) after the peak reaches the first threshold.

    The floor advances in 1 percentage-point steps and remains 2 points
    below each reached step: 10 -> 8, 11 -> 9, 12 -> 10.
    """
    try:
        peak = float(peak_net_roe_pct)
        if not math.isfinite(peak) or peak < NET_ROE_LOCK_START_PCT:
            return None, 0
        completed_steps = math.floor(
            (peak - NET_ROE_LOCK_START_PCT) / NET_ROE_LOCK_STEP_PCT + 1e-12
        )
        tier = completed_steps + 1
        floor = (
            NET_ROE_LOCK_START_PCT - NET_ROE_LOCK_GIVEBACK_PCT
            + completed_steps * NET_ROE_LOCK_STEP_PCT
        )
        return floor, tier
    except (TypeError, ValueError, OverflowError):
        return None, 0


def has_reached_net_roe_floor(current_net_roe_pct, floor_net_roe_pct):
    """Treat touching the lock floor as a trigger, with float tolerance."""
    try:
        current, floor = float(current_net_roe_pct), float(floor_net_roe_pct)
        return current < floor or math.isclose(
            current, floor, rel_tol=1e-12, abs_tol=1e-12,
        )
    except (TypeError, ValueError, OverflowError):
        return False
