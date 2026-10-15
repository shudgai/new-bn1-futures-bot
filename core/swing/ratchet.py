from __future__ import annotations

from typing import Optional

RATCHET_ACTIVATE_ROE = 5.0  # first armed level; the 5-10% micro-profit band has its own floors
RATCHET_STEP_ROE = 5.0
LIMIT_OFFSET_PCT = 0.25  # 0.2%~0.3% buffer against market-order slippage


def floor_roe(peak_roe: float) -> Optional[float]:
    """Dynamic protected ROE floor (%), None while peak ROE < 5%.

    5-7% -> 2%; 7-9% -> 3%; 9-10% -> 4.5% rising linearly to ~4.9%;
    10-15% -> 5%; 15-20% -> 10%; 20-25% -> 15%; beyond: (peak // 5) * 5 - 5.
    """
    if peak_roe < RATCHET_ACTIVATE_ROE:
        return None
    if peak_roe < 7.0:
        return 2.0
    if peak_roe < 9.0:
        return 3.0
    if peak_roe < 10.0:
        return round(4.5 + (peak_roe - 9.0) * 0.4, 2)
    if peak_roe < 15.0:
        return 5.0
    if peak_roe < 20.0:
        return 10.0
    if peak_roe < 25.0:
        return 15.0
    return float((int(peak_roe // RATCHET_STEP_ROE) * RATCHET_STEP_ROE) - RATCHET_STEP_ROE)


def limit_price(side: str, last_price: float, offset_pct: float = LIMIT_OFFSET_PCT) -> float:
    """Closing limit price: sell below last for LONG, buy above last for SHORT."""
    k = offset_pct / 100.0
    return last_price * (1 - k) if side == "LONG" else last_price * (1 + k)
