"""Shared net-ROE ladder used by scan-time and final-exit validation."""
import math


NET_ROE_LOCK_START_PCT = 10.0


def tiered_net_roe_floor(peak_net_roe_pct):
    """防震寬幅階梯鎖利（Net-ROE Ladder）：
    - 淨 ROE 達 10% 時：鎖定保本 + 手續費底線（底線 ROE = 3.0%），給予足夠價格呼吸空間。
    - 淨 ROE 達 15% 時：底線上推鎖定 10.0%。
    - 淨 ROE 達 20% 時：底線上推鎖定 15.0%，後續每 5% 步進同步上推 5%。
    """
    try:
        peak = float(peak_net_roe_pct)
        if not math.isfinite(peak) or peak < NET_ROE_LOCK_START_PCT:
            return None, 0

        if peak < 15.0:
            return 3.0, 1
        elif peak < 20.0:
            return 10.0, 2
        else:
            completed_steps = math.floor((peak - 20.0) / 5.0 + 1e-12)
            tier = 3 + completed_steps
            floor = 15.0 + completed_steps * 5.0
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
