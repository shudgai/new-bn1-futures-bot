from __future__ import annotations

from typing import Optional

RATCHET_ACTIVATE_ROE = 10.0
RATCHET_STEP_ROE = 5.0
LIMIT_OFFSET_PCT = 0.25  # 0.2%~0.3% buffer against market-order slippage


def floor_roe(peak_roe: float) -> Optional[float]:
    """Protected ROE floor (%); None while peak ROE is below the 10% activation."""
    if peak_roe < RATCHET_ACTIVATE_ROE:
        return None
    return float((int(peak_roe // RATCHET_STEP_ROE) * RATCHET_STEP_ROE) - RATCHET_STEP_ROE)


def limit_price(side: str, last_price: float, offset_pct: float = LIMIT_OFFSET_PCT) -> float:
    """Closing limit price: sell below last for LONG, buy above last for SHORT."""
    k = offset_pct / 100.0
    return last_price * (1 - k) if side == "LONG" else last_price * (1 + k)
