"""Final software-exit authorization for active high-beta contracts."""

from __future__ import annotations

import math
from typing import Any, Mapping

from core.symbol_aliases import is_auto_entry_symbol


_PROFIT_LOCK_REASONS = (
    "NET_ROE_THREE_TIER_GIVEBACK",
    "EXIT_BY_TRAILING_PROFIT_LOCK",
    "EXIT_BY_RATCHET_PROFIT_LOCK",
    "RATCHET_PROFIT_LOCK",
    "PROFIT_LOCK",
    "移動止利",
    "階梯鎖利",
    "峰值回吐",
    "獲利保護回吐",
    "最高淨利回吐",
)
_PIVOT_REASONS = {
    "LONG": "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT",
    "SHORT": "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT",
}
_LOCK_FLAGS = (
    "profit_lock_usdt_armed",
    "profit_lock_atr_armed",
    "fixed_profit_lock_pct_armed",
    "profit_bank_armed",
    "early_profit_guard_armed",
    "profit_floor_armed",
    "outer_run_pivot_protect_armed",
)


def _profit_lock_armed(
    position: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> bool:
    for source in (position, metadata):
        if any(source.get(key) is True for key in _LOCK_FLAGS):
            return True
        for key in (
            "three_tier_net_roe_lock_state",
            "peak_trailing_state",
            "dual_track_exit_state",
        ):
            state = source.get(key)
            if isinstance(state, Mapping) and (
                state.get("floor_net_roe_pct") is not None
                or state.get("profit_floor_armed") is True
                or state.get("ratchet_floor") is not None
            ):
                return True
    return False


def _net_profit_positive(position: Mapping[str, Any], price: float) -> bool:
    try:
        from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
        from core.services.exits.peak_trailing_exit import estimated_display_net_pnl

        side = str(position.get("side") or "").upper()
        entry = float(position.get("entry_price") or 0.0)
        quantity = abs(float(position.get("qty") or position.get("quantity") or 0.0))
        current = float(price)
        if (
            side not in ("LONG", "SHORT")
            or not all(math.isfinite(value) and value > 0 for value in (entry, quantity, current))
        ):
            return False
        sign = 1 if side == "LONG" else -1
        net_pnl = estimated_display_net_pnl(
            entry, current, quantity, sign, TAKER_FEE_RATE, SLIPPAGE_PCT,
        )
        return math.isfinite(float(net_pnl)) and float(net_pnl) > 0.0
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def automated_position_exit_allowed(
    symbol: str,
    position: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
    reason: str,
    price: float,
    *,
    is_manual: bool = False,
) -> bool:
    """Allow only explicit manual closes, locked net profit, or confirmed pivots."""
    if not is_auto_entry_symbol(symbol):
        return True

    if is_manual and str(reason).strip() == "手動平倉":
        return True

    meta = metadata if isinstance(metadata, Mapping) else {}
    normalized_reason = str(reason or "").upper()
    side = str(position.get("side") or "").upper()

    if (
        normalized_reason.endswith(_PIVOT_REASONS.get(side, "\0"))
        and side in _PIVOT_REASONS
    ):
        from core.services.exits.strict_true_pivot import pending_confirmed_pivot_exit

        pending = pending_confirmed_pivot_exit(dict(position), dict(meta))
        if pending is not None and pending[0] == _PIVOT_REASONS[side]:
            return True

    lock_reason = any(
        token in normalized_reason
        for token in _PROFIT_LOCK_REASONS
    )
    return bool(
        lock_reason
        and _profit_lock_armed(position, meta)
        and _net_profit_positive(position, price)
    )
