"""Final software-exit authorization for active high-beta contracts."""

from __future__ import annotations

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
    """Allow safety/manual exits or a verified, position-aligned true pivot."""
    if is_manual and str(reason).strip() == "手動平倉":
        return True

    from core.services.math_execution_gate import is_emergency_exit

    if is_emergency_exit(reason):
        from core.services.math_execution_gate import validate_exit_math

        allowed, _code, _evidence = validate_exit_math(
            position, frame, float(price), metadata, close_reason=str(reason),
        )
        return bool(allowed)

    meta = metadata if isinstance(metadata, Mapping) else {}
    normalized_reason = str(reason or "").upper()
    side = str(position.get("side") or "").upper()

    # 0. 來自 ExitGate 或 ExecutionManager 的頂級真峰谷 / 反側破軌 / 鎖利離場授權：
    # ⚠️ 嚴格鎖定：移除舊版 Wild Swing / Channel Swing 的 BEARISH_TREND_REVERSAL / BULLISH_TREND_REVERSAL / MIDLINE_BREAK_EXIT / HARD_SL，
    # 嚴禁任何繞過 ExecutionManager 的舊策略偷平倉或硬止損割肉！
    if normalized_reason.startswith("EXITGATE:"):
        if side == "LONG" and any(k in normalized_reason for k in (
            "CLOSE_LONG_AT_TOP", "CLOSE_LONG_AT_LOWER_RAIL_BREACH",
            "CLOSE_LONG_AT_PEAK_PULLBACK_2PCT", "EARLY_PIVOT_PEAK_EXIT",
            "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", "EXIT_BY_VERIFIED_FRACTAL_PEAK",
            "EXIT_LONG_AT_PEAK", "EXIT_BY_STAGED_PROFIT_LOCK_TIER1",
            "EXIT_BY_STAGED_PROFIT_LOCK_TIER2", "EXIT_BY_RATCHET_PROFIT_LOCK",
            "EXIT_BY_5U_DYNAMIC_TRAILING_LOCK",
        )):
            return True

        if side == "SHORT" and any(k in normalized_reason for k in (
            "CLOSE_SHORT_AT_BOTTOM", "CLOSE_SHORT_AT_UPPER_RAIL_BREACH",
            "CLOSE_SHORT_AT_VALLEY_REBOUND", "EARLY_PIVOT_VALLEY_EXIT",
            "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT", "EXIT_BY_VERIFIED_FRACTAL_VALLEY",
            "EXIT_SHORT_AT_BOTTOM", "EXIT_BY_STAGED_PROFIT_LOCK_TIER1",
            "EXIT_BY_STAGED_PROFIT_LOCK_TIER2", "EXIT_BY_RATCHET_PROFIT_LOCK",
            "EXIT_BY_5U_DYNAMIC_TRAILING_LOCK",
        )):
            return True

    # A lock is an explicit authorized exit class. Require the recorded net
    # ROE floor to be armed and touched so a reason string alone cannot close.
    is_profit_lock = any(token in normalized_reason for token in (
        "PROFIT_LOCK", "RATCHET", "TRAILING_LOCK", "PROFIT_FLOOR",
    ))
    if is_profit_lock:
        from core.gates.holding_protection_gate import _verified_profit_lock_details

        lock_position = dict(meta)
        lock_position.update(dict(position))
        lock_details = _verified_profit_lock_details(lock_position, float(price))
        if lock_details is not None:
            return True

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
