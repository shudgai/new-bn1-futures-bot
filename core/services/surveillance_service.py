"""Market surveillance and price monitoring services."""

from collections import deque
import math
import time
from typing import Dict, List, Optional, Tuple
import pandas as pd

from core.config import (
    CONTINUOUS_ENTRY_OUTER_ZONE_RATIO,
    FULL_MARKET_SURVEILLANCE_ENABLED,
    FULL_MARKET_SURVEILLANCE_LONG_WINDOW_SEC,
    FULL_MARKET_SURVEILLANCE_STEADY_WINDOW_SEC,
)

def sample_reference_price(samples: deque, cutoff: float) -> Optional[float]:
    for sample_at, price in reversed(samples):
        if sample_at <= cutoff:
            return float(price)
    return None

def market_crash_entries_paused(cooldown_until: float, now: Optional[float] = None) -> bool:
    return float(now if now is not None else time.time()) < float(cooldown_until or 0.0)

def btc_flash_crash_close_symbols(
    positions: dict, position_meta: Optional[dict] = None, side: str = "LONG",
) -> List[str]:
    """All positions on the threatened side, including Channel Swing."""
    del position_meta
    threatened_side = str(side or "").upper()
    return [
        symbol for symbol, position in positions.items()
        if str(position.get("side") or "").upper() == threatened_side
    ]

def continuous_entry_price_is_safe(
    side: str, frame: pd.DataFrame, live_price: float,
) -> Tuple[bool, str]:
    """Reject market entries near the directional KC extreme."""
    required = {"kc_upper", "kc_lower"}
    if frame is None or frame.empty or not required.issubset(frame.columns):
        return False, "KC data unavailable"
    middle_column = (
        "kc_middle" if "kc_middle" in frame.columns
        else "ema_20" if "ema_20" in frame.columns
        else None
    )
    if middle_column is None:
        return False, "KC middle data unavailable"
    row = frame.iloc[-1]
    try:
        price = float(live_price)
        middle = float(row[middle_column])
        upper = float(row["kc_upper"])
        lower = float(row["kc_lower"])
    except (TypeError, ValueError):
        return False, "KC data invalid"
    if not all(math.isfinite(value) for value in (price, middle, upper, lower)):
        return False, "KC data invalid"
    if not lower < middle < upper:
        return False, "KC channel invalid"

    side = str(side or "").upper()
    if side == "LONG":
        limit = middle + (upper - middle) * CONTINUOUS_ENTRY_OUTER_ZONE_RATIO
        safe = price < limit
        reason = f"price {price:.8g} is in the upper KC chase zone (limit {limit:.8g})"
    elif side == "SHORT":
        limit = middle - (middle - lower) * CONTINUOUS_ENTRY_OUTER_ZONE_RATIO
        safe = price > limit
        reason = f"price {price:.8g} is in the lower KC chase zone (limit {limit:.8g})"
    else:
        return False, f"invalid entry side {side}"
    return (True, "price is safe") if safe else (False, reason)
