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
    """Authorize only manual, safety, locked-profit, or validated KC exits."""
    if not is_auto_entry_symbol(symbol):
        return True

    if is_manual and str(reason).strip() == "手動平倉":
        return True

    meta = metadata if isinstance(metadata, Mapping) else {}
    normalized_reason = str(reason or "").upper()
    if any(
        token in normalized_reason
        for token in (
            "HARD_STOP",
            "CIRCUIT_BREAKER",
        )
    ):
        return True

    side = str(position.get("side") or "").upper()
    entry_mode = str(
        position.get("entry_mode") or meta.get("entry_mode") or ""
    ).upper()
    short_exit_state = (
        position.get("channel_short_oversold_exit_state")
        or meta.get("channel_short_oversold_exit_state")
        or {}
    )
    trend_hold_active = (
        position.get("channel_short_bearish_trend_hold_active") is True
        or meta.get("channel_short_bearish_trend_hold_active") is True
        or (
            isinstance(short_exit_state, Mapping)
            and short_exit_state.get("bearish_trend_hold_active") is True
        )
    )
    if side == "SHORT" and entry_mode in {"CHANNEL_SWING", "WILD_SWING"}:
        lock_reason = any(
            token in normalized_reason for token in _PROFIT_LOCK_REASONS
        ) or "BREAKEVEN" in normalized_reason
        if lock_reason:
            armed = _profit_lock_armed(position, meta) or any(
                source.get(key) is True
                for source in (position, meta)
                for key in ("breakeven_locked", "is_breakeven_moved")
            )
            return bool(armed and _net_profit_positive(position, price))

        from core.services.exits.kc_true_extreme_exit import (
            STATE_KEY as KC_EXIT_STATE_KEY,
            pending_kc_true_extreme_exit,
        )

        pending = pending_kc_true_extreme_exit(dict(position), dict(meta))
        if pending is None or pending[0] != "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL":
            return False
        details = pending[1]
        state = position.get(KC_EXIT_STATE_KEY) or meta.get(KC_EXIT_STATE_KEY)
        return bool(
            isinstance(state, Mapping)
            and state.get("armed") is True
            and details.get("cause") in {
                "BULLISH_ENGULFING", "INTRADAY_TROUGH_REBOUND",
            }
            and "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL" in normalized_reason
        )
    if side == "SHORT" and entry_mode == "CHANNEL_SWING":
        if trend_hold_active:
            lock_reason = any(
                token in normalized_reason
                for token in _PROFIT_LOCK_REASONS
            )
            return bool(
                lock_reason
                and _profit_lock_armed(position, meta)
                and _net_profit_positive(position, price)
            )
        if (
            isinstance(short_exit_state, Mapping)
            and short_exit_state.get("armed") is True
        ):
            if (
                "CHANNEL_SHORT_OVERSOLD_REVERSAL_PARTIAL_50"
                in normalized_reason
                and short_exit_state.get("reversal_confirmed") is True
                and short_exit_state.get("partial_exit_pending") is True
                and short_exit_state.get("partial_exit_done") is not True
            ):
                return True
            if (
                "CHANNEL_SHORT_OVERSOLD_RATCHET_EXIT" in normalized_reason
                and short_exit_state.get("full_exit_pending") is True
            ):
                return True

    from core.services.exits.kc_true_extreme_exit import (
        pending_kc_true_extreme_exit,
    )

    kc_exit = pending_kc_true_extreme_exit(dict(position), dict(meta))
    if kc_exit is not None and kc_exit[0] in normalized_reason:
        return True

    if "BREAKEVEN" in normalized_reason and any(
        source.get(key) is True
        for source in (position, meta)
        for key in ("breakeven_locked", "is_breakeven_moved")
    ):
        return True

    lock_reason = any(
        token in normalized_reason
        for token in _PROFIT_LOCK_REASONS
    )
    if lock_reason:
        return bool(
            _profit_lock_armed(position, meta)
            and _net_profit_positive(position, price)
        )

    return False
