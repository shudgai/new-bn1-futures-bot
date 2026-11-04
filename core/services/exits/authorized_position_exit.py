"""Final software-exit authorization for active high-beta contracts."""

from __future__ import annotations

import math
import logging
import time
from typing import Any, Mapping


def meme_strategy_disaster_disabled(symbol: str, reason: str) -> bool:
    from core.symbol_aliases import is_meme_breakout_symbol
    from core.services.math_execution_gate import is_emergency_exit

    normalized = str(reason).upper()
    if not is_meme_breakout_symbol(symbol) or not is_emergency_exit(reason):
        return False
    if normalized == "EXIT_BY_EXTREME_WATERFALL":
        return False
    if any(token in normalized for token in (
        "WATERFALL", "TWO_CLOSED_ADVERSE_ABNORMAL", "CATASTROPHIC",
        "OPPOSITE_KC_BAND_BREACH", "反側KC",
        "ADVERSE_STOP_LOSS", "EXIT_LONG_AT_PEAK", "EXIT_SHORT_AT_BOTTOM",
        "急速反向", "閃崩", "瀑布",
    )):
        return True
    return not any(token in normalized for token in (
        "LIQUIDATION", "DATA_OUTAGE",
        "ACCOUNT_CIRCUIT_BREAKER", "ACCOUNT_HARD_STOP", "帳戶硬止損", "MARGIN_LOSS",
    ))


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
        logging.getLogger(__name__).info(
            "[VETO_HOLD_%s] reason=ENTRY_CANDLE opened=%s", position.get("side"), opened,
        )
        return "VETO_HOLD_ENTRY_CANDLE"
    return None


class UltimateExitGatekeeper:
    """Shared final veto; no signal label can substitute for market evidence."""

    @staticmethod
    def can_execute_exit(
        symbol: str,
        position: Mapping[str, Any],
        metadata: Mapping[str, Any] | None,
        reason: str,
        price: float,
        *,
        is_manual: bool = False,
        frame: Any = None,
        is_bar_closed: bool | None = None,
    ) -> bool:
        logger = logging.getLogger(__name__)
        if is_manual and str(reason).strip() == "手動平倉":
            return True
        try:
            exit_price = float(price)
        except (TypeError, ValueError, OverflowError) as exc:
            logger.error("ULTIMATE_EXIT_INVALID_PRICE symbol=%s error=%s", symbol, exc)
            return False
        if not math.isfinite(exit_price) or exit_price <= 0:
            logger.error("ULTIMATE_EXIT_INVALID_PRICE symbol=%s price=%s", symbol, price)
            return False

        from core.gates.ultimate_short_exit_gatekeeper import (
            DEFER, DISASTER_RAIL_REASONS, UltimateShortExitGatekeeper,
        )
        from core.symbol_aliases import is_meme_breakout_symbol

        if is_meme_breakout_symbol(symbol):
            side = str(position.get("side") or "").upper()
            rail_reason = DISASTER_RAIL_REASONS.get(side)
            if rail_reason:
                rail_verdict = UltimateShortExitGatekeeper.evaluate(
                    symbol, position, metadata, rail_reason, exit_price,
                    frame=frame, is_bar_closed=is_bar_closed,
                )
                if rail_verdict.allowed and rail_verdict.code == rail_reason:
                    return True
        meme_verdict = UltimateShortExitGatekeeper.evaluate(
            symbol, position, metadata, reason, exit_price,
            frame=frame, is_bar_closed=is_bar_closed,
        )
        if meme_verdict.verdict != DEFER:
            return meme_verdict.allowed

        from core.services.math_execution_gate import is_emergency_exit

        if meme_strategy_disaster_disabled(symbol, reason):
            logger.info(
                "[VETO_HOLD_%s] DISABLED_MEME_STRATEGY_DISASTER symbol=%s reason=%s",
                position.get("side"), symbol, reason,
            )
            return False
        if is_emergency_exit(reason):
            from core.services.math_execution_gate import validate_exit_math

            allowed, _code, _evidence = validate_exit_math(
                {**position, "symbol": symbol}, frame, exit_price, metadata, close_reason=str(reason),
            )
            return bool(allowed)
        if is_bar_closed is not None and is_bar_closed is not True:
            logger.info("[VETO_HOLD_%s] UNCONFIRMED_EXIT_BAR symbol=%s", position.get("side"), symbol)
            return False
        if strategy_exit_bar_block_reason(position, metadata):
            return False

        normalized_reason = str(reason or "").upper()
        from core.services.exits.strict_true_pivot import (
            PROFIT_PIVOT_REASONS, profitable_true_pivot_exit,
        )
        if normalized_reason in PROFIT_PIVOT_REASONS:
            pivot = profitable_true_pivot_exit(
                {**position, "symbol": symbol}, frame, dict(metadata or {}), quote=exit_price,
            )
            return pivot is not None and pivot[0] == normalized_reason

        logger.info(
            "[VETO_HOLD_%s] UNAUTHORIZED_EXIT_REASON symbol=%s reason=%s",
            position.get("side"), symbol, reason,
        )
        return False


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
    return UltimateExitGatekeeper.can_execute_exit(
        symbol, position, metadata, reason, price, is_manual=is_manual, frame=frame,
    )
