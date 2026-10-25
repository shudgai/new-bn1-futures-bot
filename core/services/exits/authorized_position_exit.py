"""Final software-exit authorization for active high-beta contracts."""

from __future__ import annotations

from typing import Any, Mapping

from core.symbol_aliases import is_auto_entry_symbol


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
    """Allow safety/manual exits or a verified, position-aligned true pivot."""
    if not is_auto_entry_symbol(symbol):
        return True

    if is_manual and str(reason).strip() == "手動平倉":
        return True

    from core.services.math_execution_gate import is_emergency_exit

    if is_emergency_exit(reason):
        return True

    meta = metadata if isinstance(metadata, Mapping) else {}
    normalized_reason = str(reason or "").upper()
    side = str(position.get("side") or "").upper()
    if frame is not None:
        from core.gates.holding_protection_gate import has_confirmed_true_pivot

        has_pivot, pivot_type, _ = has_confirmed_true_pivot(
            dict(position), frame, float(price),
        )
        expected_prefix = "TRUE_PEAK" if side == "LONG" else "TRUE_VALLEY"
        if has_pivot and str(pivot_type or "").upper().startswith(expected_prefix):
            return True

    from core.services.exits.strict_true_pivot import pending_confirmed_pivot_exit

    confirmed_pivot = pending_confirmed_pivot_exit(dict(position), dict(meta))
    if confirmed_pivot is not None and confirmed_pivot[0] in normalized_reason:
        expected_reason = (
            "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT"
            if side == "LONG"
            else "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
            if side == "SHORT"
            else ""
        )
        return confirmed_pivot[0] == expected_reason

    from core.services.exits.kc_true_extreme_exit import (
        pending_kc_true_extreme_exit,
    )

    kc_exit = pending_kc_true_extreme_exit(dict(position), dict(meta))
    if kc_exit is not None and kc_exit[0] in normalized_reason:
        details = kc_exit[1]
        state = position.get("kc_true_extreme_exit_state") or meta.get(
            "kc_true_extreme_exit_state"
        )
        expected_reason = (
            "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL"
            if side == "LONG"
            else "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL"
            if side == "SHORT"
            else ""
        )
        return bool(
            kc_exit[0] == expected_reason
            and details.get("cause") == "THREE_POINT_CONFIRMED_PIVOT"
            and isinstance(state, Mapping)
            and state.get("armed") is True
            and kc_exit[0] in normalized_reason
        )

    # Profit locks, midline reclaim, MA turns, and pullback labels are not
    # pivot evidence. They may annotate a pending exit, but cannot authorize it.
    return False
