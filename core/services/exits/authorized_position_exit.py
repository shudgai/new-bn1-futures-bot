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
    """Allow safety/manual exits or a profitable, completed true pivot."""
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
    from core.services.exits.strict_true_pivot import (
        PROFIT_PIVOT_REASONS, profitable_true_pivot_exit,
    )

    if normalized_reason in PROFIT_PIVOT_REASONS:
        pivot = profitable_true_pivot_exit(
            dict(position), frame, dict(metadata or {}), quote=exit_price,
        )
        return pivot is not None and pivot[0] == normalized_reason

    # Old KC/pullback/profit-lock labels cannot replace true-pivot authority.
    return False
