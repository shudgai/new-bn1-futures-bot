"""Final software-exit authorization for active high-beta contracts."""

from __future__ import annotations

import math
import logging
import time
from typing import Any, Mapping


def strategy_exit_bar_block_reason(
    position: Mapping[str, Any], metadata: Mapping[str, Any] | None = None,
) -> str | None:
    """Use actual fill time and UTC minutes, never a signal or cached candle."""
    raw = position.get("open_timestamp")
    if raw is None:
        raw = (metadata or {}).get("open_timestamp")
    try:
        opened = float(raw)
        if opened >= 1e11:
            opened /= 1000.0
        now = time.time()
        if not math.isfinite(opened) or opened <= 0 or opened > now:
            raise ValueError("invalid or future position open time")
    except (TypeError, ValueError, OverflowError) as exc:
        logging.getLogger(__name__).error("VETO_EXIT_OPEN_TIME_INVALID: %s", exc)
        return "VETO_EXIT_OPEN_TIME_INVALID"
    if int(opened // 60) == int(now // 60):
        logging.getLogger(__name__).info(
            "VETO_HOLD_ENTRY_CANDLE side=%s opened=%s", position.get("side"), opened,
        )
        return "VETO_HOLD_ENTRY_CANDLE"
    return None


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

    if strategy_exit_bar_block_reason(position, metadata):
        return False

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
