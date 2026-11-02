"""Final software-exit authorization for active high-beta contracts."""

from __future__ import annotations

import math
from typing import Any, Mapping


def automated_position_exit_allowed(
    symbol: str,
    position: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
    reason: str,
    price: float,
    *,
    is_manual: bool = False,
    frame: Any = None,
) -> bool:
    """Allow safety/manual exits or an authoritative closed KC reversal."""
    if is_manual and str(reason).strip() == "手動平倉":
        return True

    try:
        exit_price = float(price)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(exit_price) or exit_price <= 0:
        return False

    from core.services.math_execution_gate import is_emergency_exit

    if is_emergency_exit(reason):
        from core.services.math_execution_gate import validate_exit_math

        allowed, _code, _evidence = validate_exit_math(
            position, frame, exit_price, metadata, close_reason=str(reason),
        )
        return bool(allowed)

    normalized_reason = str(reason or "").upper()
    from core.services.exits.trend_lock import KC_WAVE_EXIT_REASON, confirmed_kc_wave_exit

    if normalized_reason == KC_WAVE_EXIT_REASON:
        return confirmed_kc_wave_exit(position, frame) is not None

    # Old pivot/pullback pending flags cannot authorize an ordinary wave exit.
    return False
