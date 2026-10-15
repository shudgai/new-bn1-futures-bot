from __future__ import annotations

from typing import Optional

RATCHET_ACTIVATE_ROE = 10.0
RATCHET_STEP_ROE = 5.0
MAX_GIVEBACK_HIGH = 5.0  # peak >= 20%: never give back more than 5 points
LIMIT_OFFSET_PCT = 0.25  # 0.2%~0.3% buffer against market-order slippage


def floor_roe(peak_roe: float) -> Optional[float]:
    """Compact ladder: None below 10%; 10-15% -> 7%; 15-20% -> 12%; >=20% -> peak - 5%."""
    if peak_roe < RATCHET_ACTIVATE_ROE:
        return None
    if peak_roe < 15.0:
        return 7.0
    if peak_roe < 20.0:
        return 12.0
    return float(peak_roe - MAX_GIVEBACK_HIGH)


def limit_price(side: str, last_price: float, offset_pct: float = LIMIT_OFFSET_PCT) -> float:
    """Closing limit price: sell below last for LONG, buy above last for SHORT."""
    k = offset_pct / 100.0
    return last_price * (1 - k) if side == "LONG" else last_price * (1 + k)
