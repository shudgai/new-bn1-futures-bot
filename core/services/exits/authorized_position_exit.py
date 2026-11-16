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
    if any(token in normalized for token in (
        "WATERFALL", "CATASTROPHIC_DUMP", "CATASTROPHIC_PUMP",
        "EMERGENCY_SURGE", "閃崩", "瀑布",
    )):
        return False
    if any(token in normalized for token in (
        "LIQUIDATION", "DATA_OUTAGE", "ACCOUNT_CIRCUIT_BREAKER",
        "CIRCUIT_BREAKER", "ACCOUNT_HARD_STOP", "HARD_STOP_LOSS",
        "HARD_STOP", "MARGIN_LOSS", "MAXIMUM_LOSS",
        "OPPOSITE_KC_BAND_BREACH", "反側KC",
        "EXIT_BY_TWO_CLOSED_ADVERSE_ABNORMAL",
        "EXIT_LONG_EMERGENCY_WATERFALL", "EXIT_LONG_ON_TOP_WATERFALL_DUMP",
    )):
        return False
    if any(token in normalized for token in (
        "CATASTROPHIC", "ADVERSE_STOP_LOSS",
        "EXIT_LONG_AT_PEAK", "EXIT_SHORT_AT_BOTTOM", "急速反向",
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


def is_dual_trend_emergency_exit(reason: str) -> bool:
    normalized = str(reason or "").upper().replace(" ", "_")
    return any(token in normalized for token in (
        "ACCOUNT_HARD_STOP", "ACCOUNT_CIRCUIT_BREAKER",
        "CIRCUIT_BREAKER_HARD_SL", "EXIT_BY_CIRCUIT_BREAKER_HARD_SL",
        "CIRCUIT_BREAKER", "HARD_STOP_LOSS", "HARD_STOP",
        "LIQUIDATION", "MARGIN_LOSS", "DATA_OUTAGE_HARD_STOP",
        "CIRCUIT_BREAKER_EMERGENCY_DATA_OUTAGE",
        "EXIT_BY_EXTREME_WATERFALL", "EXIT_LONG_EMERGENCY_WATERFALL",
        "EXIT_SHORT_EMERGENCY_SURGE", "EMERGENCY_DUMP_EXIT",
        "EMERGENCY_EXTREME_ADVERSE_BAR", "EXIT_BY_TWO_CLOSED_ADVERSE_ABNORMAL",
        "EXIT_LONG_ON_TOP_WATERFALL_DUMP", "FAIL_SAFE_EMERGENCY_EXIT",
        "OPPOSITE_KC_BAND_BREACH", "CATASTROPHIC_DUMP", "CATASTROPHIC_PUMP",
        "WATERFALL", "EMERGENCY_SURGE", "閃崩", "瀑布", "極端瀑布", "極端閃崩",
    ))


def macro_kc_midline_exit_block_reason(
    position: Mapping[str, Any],
    frame: Any,
    metadata: Mapping[str, Any] | None = None,
) -> str | None:
    """Require a post-entry, closed higher-timeframe KC midline reversal."""
    import pandas as pd

    side = str(position.get("side") or "").upper()
    if side not in ("LONG", "SHORT") or not isinstance(frame, pd.DataFrame):
        return "VETO_EXIT_MACRO_KC_DATA_UNAVAILABLE"

    attrs = getattr(frame, "attrs", {})
    try:
        snapshot_ms = float(attrs.get("snapshot_ms", time.time() * 1000.0))
        if snapshot_ms < 1e11:
            snapshot_ms *= 1000.0
    except (TypeError, ValueError, OverflowError):
        return "VETO_EXIT_MACRO_KC_DATA_UNAVAILABLE"
    if not math.isfinite(snapshot_ms) or snapshot_ms <= 0:
        return "VETO_EXIT_MACRO_KC_DATA_UNAVAILABLE"

    raw_interval = attrs.get("timeframe_ms", 60_000)
    try:
        raw_interval = float(raw_interval)
    except (TypeError, ValueError, OverflowError):
        return "VETO_EXIT_MACRO_KC_DATA_UNAVAILABLE"
    if raw_interval < 1_000:
        raw_interval *= 1_000.0
    if not math.isfinite(raw_interval) or raw_interval != 60_000:
        return "VETO_EXIT_MACRO_KC_DATA_UNAVAILABLE"

    from core.services.candle_data import closed_entry_candles
    from core.services.low_latency_exit import resample_1m_to_5m

    one_minute = closed_entry_candles(frame)
    if len(one_minute) < 100:
        return "VETO_EXIT_MACRO_KC_DATA_UNAVAILABLE"
    for key in ("macro_frame", "frame_5m"):
        one_minute.attrs.pop(key, None)
    macro_frame = resample_1m_to_5m(one_minute)
    if macro_frame.empty:
        return "VETO_EXIT_MACRO_KC_DATA_UNAVAILABLE"
    macro_frame["is_closed"] = (
        pd.to_numeric(macro_frame["timestamp"], errors="coerce") + 300_000
        <= snapshot_ms
    )
    macro_frame.attrs["timeframe_ms"] = 300_000
    macro_frame.attrs["snapshot_ms"] = snapshot_ms
    closed_macro = closed_entry_candles(macro_frame)
    if len(closed_macro) < 20:
        return "VETO_EXIT_MACRO_KC_DATA_UNAVAILABLE"
    latest_stamp = float(closed_macro.iloc[-1]["timestamp"])
    latest_close_ms = latest_stamp + 300_000
    if (
        not math.isfinite(latest_close_ms)
        or latest_close_ms > snapshot_ms
        or snapshot_ms - latest_close_ms > 600_000
    ):
        return "VETO_EXIT_MACRO_KC_DATA_STALE"

    raw_opened = position.get("open_timestamp")
    if raw_opened is None:
        raw_opened = (metadata or {}).get("open_timestamp")
    try:
        opened_ms = float(raw_opened)
        if opened_ms < 1e11:
            opened_ms *= 1000.0
    except (TypeError, ValueError, OverflowError):
        return "VETO_EXIT_OPEN_TIME_INVALID"
    if not math.isfinite(opened_ms) or opened_ms <= 0:
        return "VETO_EXIT_OPEN_TIME_INVALID"
    if latest_close_ms <= opened_ms:
        return "VETO_EXIT_NO_POST_ENTRY_MACRO_KC_CONFIRMATION"

    middle = pd.to_numeric(closed_macro["close"], errors="coerce").ewm(
        span=20, adjust=False,
    ).mean()
    try:
        previous_middle, latest_middle = map(float, middle.iloc[-2:])
    except (TypeError, ValueError, OverflowError):
        return "VETO_EXIT_MACRO_KC_DATA_UNAVAILABLE"
    if not all(math.isfinite(value) and value > 0 for value in (previous_middle, latest_middle)):
        return "VETO_EXIT_MACRO_KC_DATA_UNAVAILABLE"

    macro_reversed = (
        (side == "LONG" and latest_middle < previous_middle)
        or (side == "SHORT" and latest_middle > previous_middle)
    )
    return None if macro_reversed else "VETO_EXIT_MACRO_KC_MIDLINE_NOT_REVERSED"


def dual_trend_exit_block_reason(
    position: Mapping[str, Any],
    frame: Any,
    metadata: Mapping[str, Any] | None = None,
) -> str | None:
    """Require post-entry closed KC reversal and an opposing HTF macro trend."""
    import pandas as pd

    side = str(position.get("side") or "").upper()
    if side not in ("LONG", "SHORT") or not isinstance(frame, pd.DataFrame):
        return "VETO_EXIT_TREND_DATA_UNAVAILABLE"

    from core.services.candle_data import (
        closed_entry_candles, confirmed_kc_midline_slope,
    )

    closed = closed_entry_candles(frame)
    slope = confirmed_kc_midline_slope(frame)
    if slope is None or len(closed) < 2:
        return "VETO_EXIT_TREND_DATA_UNAVAILABLE"

    raw_opened = position.get("open_timestamp")
    if raw_opened is None:
        raw_opened = (metadata or {}).get("open_timestamp")
    if raw_opened is None:
        return "VETO_EXIT_OPEN_TIME_INVALID"
    try:
        opened_ms = float(raw_opened)
        if opened_ms < 1e11:
            opened_ms *= 1000.0
        latest_stamp = float(closed.iloc[-1]["timestamp"])
        if latest_stamp < 1e11:
            latest_stamp *= 1000.0
        interval = float(frame.attrs.get("timeframe_ms", 60_000))
        snapshot_ms = float(frame.attrs.get("snapshot_ms", time.time() * 1000.0))
    except (KeyError, TypeError, ValueError, OverflowError):
        return "VETO_EXIT_TREND_DATA_UNAVAILABLE"
    if snapshot_ms < 1e11:
        snapshot_ms *= 1000.0
    if not all(math.isfinite(value) and value > 0 for value in (opened_ms, latest_stamp, interval, snapshot_ms)):
        return "VETO_EXIT_TREND_DATA_UNAVAILABLE"
    if interval < 1_000:
        interval *= 1_000.0
    latest_close_ms = latest_stamp + interval
    if latest_close_ms <= opened_ms:
        return "VETO_EXIT_NO_POST_ENTRY_KC_CONFIRMATION"
    if latest_close_ms > snapshot_ms or snapshot_ms - latest_close_ms > 180_000:
        return "VETO_EXIT_TREND_DATA_STALE"

    attrs = getattr(frame, "attrs", {})
    macro_trend = str(
        attrs.get("macro_trend_htf", attrs.get("macro_trend", ""))
    ).upper()
    if macro_trend not in ("BULLISH", "BEARISH"):
        return "VETO_EXIT_MACRO_TREND_UNKNOWN"

    kc_reversed = (side == "LONG" and slope < 0) or (side == "SHORT" and slope > 0)
    macro_reversed = (
        (side == "LONG" and macro_trend == "BEARISH")
        or (side == "SHORT" and macro_trend == "BULLISH")
    )
    if not kc_reversed:
        return "VETO_EXIT_KC_TREND_NOT_REVERSED"
    if not macro_reversed:
        return "VETO_EXIT_MACRO_TREND_NOT_REVERSED"
    macro_kc_block = macro_kc_midline_exit_block_reason(position, frame, metadata)
    if macro_kc_block is not None:
        return macro_kc_block
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

        normalized_reason = str(reason or "").upper()
        from core.services.mtf_kc_state_machine import (
            MTF_KC_EXIT_REASON_PREFIX,
            mtf_exit_is_authorized,
        )
        if normalized_reason.startswith(MTF_KC_EXIT_REASON_PREFIX):
            return mtf_exit_is_authorized(
                metadata,
                reason,
                exit_price,
                str(position.get("side") or "").upper(),
            )

        from core.services.exits.strict_true_pivot import (
            PROFIT_PIVOT_REASONS, profitable_true_pivot_exit,
        )
        if normalized_reason in PROFIT_PIVOT_REASONS:
            if is_bar_closed is not None and is_bar_closed is not True:
                return False
            if meme_strategy_disaster_disabled(symbol, reason):
                return False
            if strategy_exit_bar_block_reason(position, metadata):
                return False
            pivot = profitable_true_pivot_exit(
                {**position, "symbol": symbol},
                frame,
                dict(metadata or {}),
                quote=exit_price,
            )
            return pivot is not None and pivot[0] == normalized_reason

        if not is_dual_trend_emergency_exit(reason):
            trend_block = dual_trend_exit_block_reason(position, frame, metadata)
            if trend_block is not None:
                logger.info(
                    "[VETO_HOLD_%s] %s symbol=%s reason=%s",
                    position.get("side"), trend_block, symbol, reason,
                )
                return False

        if is_dual_trend_emergency_exit(reason):
            from core.services.math_execution_gate import (
                is_emergency_exit, validate_exit_math,
            )

            if is_emergency_exit(reason):
                if meme_strategy_disaster_disabled(symbol, reason):
                    return False
                allowed, _code, _evidence = validate_exit_math(
                    {**position, "symbol": symbol}, frame, exit_price, metadata,
                    close_reason=str(reason),
                )
                return bool(allowed)

        normalized_reason = str(reason or "").upper()
        from core.services.exits.kc_true_extreme_exit import (
            pending_kc_true_extreme_exit,
        )
        pending_kc_exit = pending_kc_true_extreme_exit(position, metadata)
        if (
            pending_kc_exit is not None
            and pending_kc_exit[1].get("cause") == "THREE_POINT_CONFIRMED_PIVOT"
            and normalized_reason.endswith(str(pending_kc_exit[0]).upper())
        ):
            return True

        # 🚀 ACTIVE_EXIT 授權：主動鎖盈平倉、峰頂吞噬/RSI下彎、追蹤止盈、破MA5及時平倉
        if "ACTIVE_EXIT" in str(reason).upper():
            logger.info("⚡ [ULTIMATE_EXIT_ALLOWED] symbol=%s ACTIVE_EXIT 授權通過: reason=%s", symbol, reason)
            return True

        from core.gates.ultimate_short_exit_gatekeeper import (
            DEFER, UltimateShortExitGatekeeper,
        )

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
