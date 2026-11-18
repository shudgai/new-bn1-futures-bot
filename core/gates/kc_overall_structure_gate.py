"""Fail-closed Keltner channel alignment gate for automated entries."""

import logging
import math
import time
from typing import Any

import numpy as np
import pandas as pd

from core.services.candle_data import closed_entry_candles


HTF_SLOPE_THRESHOLD = 0.02
LTF_SHORT_SLOPE_MIN = 0.03
LTF_LONG_SLOPE_MIN = 0.08
MAX_ENTRY_BAND_RATIO = 1.05
KC_OVERALL_UP_ALIGNED = "UP_ALIGNED"
KC_OVERALL_DOWN_ALIGNED = "DOWN_ALIGNED"
KC_OVERALL_CHOP_BOX = "CHOP_BOX"
LONG_SLOPE_LOOKBACK = 15
_logger = logging.getLogger(__name__)


def _finite(row: Any, key: str) -> float | None:
    try:
        value = float(row.get(key))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _timestamp_ms(value: Any) -> float | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(timestamp) or timestamp <= 0:
        return None
    return timestamp if timestamp >= 1e11 else timestamp * 1000.0


def _live_frame_problem(
    frame: Any, timeframe_ms: int, minimum_rows: int, now_ms: float,
) -> str | None:
    if not isinstance(frame, pd.DataFrame) or len(frame) < minimum_rows:
        return "BLOCKED_ENTRY_FRAME_INSUFFICIENT"
    attrs = getattr(frame, "attrs", {})
    if attrs.get("timeframe_ms") != timeframe_ms:
        return "BLOCKED_ENTRY_TIMEFRAME_INVALID"
    try:
        snapshot_ms = float(attrs["snapshot_ms"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return "BLOCKED_ENTRY_SNAPSHOT_INVALID"
    if snapshot_ms < 1e11:
        snapshot_ms *= 1000.0
    if (
        not math.isfinite(snapshot_ms)
        or not 0 <= now_ms - snapshot_ms <= 180_000
    ):
        return "BLOCKED_ENTRY_SNAPSHOT_STALE"

    tail = frame.tail(minimum_rows)
    timestamps = (
        [_timestamp_ms(value) for value in tail["timestamp"]]
        if "timestamp" in tail else []
    )
    if (
        len(timestamps) != minimum_rows
        or any(value is None for value in timestamps)
        or any(
            timestamps[index] - timestamps[index - 1] != timeframe_ms
            for index in range(1, len(timestamps))
        )
    ):
        return "BLOCKED_ENTRY_CANDLE_SEQUENCE_INVALID"
    current_ms = timestamps[-1]
    expected_now_bar = math.floor(now_ms / timeframe_ms) * timeframe_ms
    expected_snap_bar = math.floor(snapshot_ms / timeframe_ms) * timeframe_ms
    if (
        current_ms not in (expected_now_bar, expected_snap_bar)
        or current_ms > snapshot_ms
        or current_ms + timeframe_ms <= snapshot_ms
    ):
        return "BLOCKED_ENTRY_LIVE_CANDLE_INVALID"
    if "is_closed" not in tail:
        return "BLOCKED_ENTRY_CANDLE_FINALITY_INVALID"
    closed = tail["is_closed"].tolist()
    if (
        not all(
            isinstance(value, (bool, np.bool_)) and bool(value)
            for value in closed[:-1]
        )
        or not isinstance(closed[-1], (bool, np.bool_))
        or bool(closed[-1])
    ):
        return "BLOCKED_ENTRY_CANDLE_FINALITY_INVALID"
    return None


def evaluate_entry_signal(
    side: str,
    frame_1m: pd.DataFrame,
    frame_15m: pd.DataFrame,
    current_price: float,
    *,
    symbol: str | None = None,
    account: Any = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Revalidate both timeframes and live entry guards using current market data."""
    side = str(side or "").upper()
    if side not in ("LONG", "SHORT"):
        return {"allowed": False, "reason": "BLOCKED_ENTRY_SIDE_INVALID"}
    try:
        quote = float(current_price)
    except (TypeError, ValueError, OverflowError):
        return {"allowed": False, "reason": "BLOCKED_ENTRY_QUOTE_INVALID"}
    try:
        current_time = time.time() if now is None else float(now)
    except (TypeError, ValueError, OverflowError):
        return {"allowed": False, "reason": "BLOCKED_ENTRY_QUOTE_INVALID"}
    if not math.isfinite(current_time) or not math.isfinite(quote) or quote <= 0:
        return {"allowed": False, "reason": "BLOCKED_ENTRY_QUOTE_INVALID"}
    now_ms = current_time * 1000.0

    if account is not None and symbol:
        position = getattr(account, "positions", {}).get(symbol)
        if (
            isinstance(position, dict)
            and str(position.get("side") or "").upper() == side
        ):
            return {"allowed": False, "reason": "BLOCKED_ENTRY_SIDE_ALREADY_OPEN"}
        from core.services.exits.priority_exit_manager import (
            entry_circuit_breaker_cooldown_problem,
        )

        cooldown_problem = entry_circuit_breaker_cooldown_problem(
            account, symbol, now=current_time,
        )
        if cooldown_problem:
            return {"allowed": False, "reason": cooldown_problem}

    ltf_problem = _live_frame_problem(frame_1m, 60_000, 12, now_ms)
    if ltf_problem:
        return {"allowed": False, "reason": ltf_problem}
    htf_problem = _live_frame_problem(frame_15m, 900_000, 2, now_ms)
    if htf_problem:
        return {"allowed": False, "reason": htf_problem}

    live = frame_1m.iloc[-1]
    previous = frame_1m.iloc[-2]
    htf_live = frame_15m.iloc[-1]
    htf_previous = frame_15m.iloc[-2]
    required_ltf = (
        "open", "kc_middle", "kc_upper", "kc_lower", "atr", "ma5", "ma15",
    )
    if any(field not in frame_1m for field in required_ltf):
        return {"allowed": False, "reason": "BLOCKED_ENTRY_LTF_INDICATORS_MISSING"}
    required_htf = ("kc_middle", "atr")
    if any(field not in frame_15m for field in required_htf):
        return {"allowed": False, "reason": "BLOCKED_ENTRY_HTF_INDICATORS_MISSING"}

    values = {
        "quote": quote,
        "open": _finite(live, "open"),
        "ltf_middle": _finite(live, "kc_middle"),
        "ltf_upper": _finite(live, "kc_upper"),
        "ltf_lower": _finite(live, "kc_lower"),
        "ltf_atr": _finite(live, "atr"),
        "ma5_current": _finite(live, "ma5"),
        "ma5_previous": _finite(previous, "ma5"),
        "ma15": _finite(live, "ma15"),
        "htf_middle_current": _finite(htf_live, "kc_middle"),
        "htf_middle_previous": _finite(htf_previous, "kc_middle"),
        "htf_atr": _finite(htf_live, "atr"),
        "upper_previous_2": _finite(frame_1m.iloc[-3], "kc_upper"),
        "lower_previous_2": _finite(frame_1m.iloc[-3], "kc_lower"),
        "short_middle": _finite(frame_1m.iloc[-3], "kc_middle"),
        "long_middle": _finite(frame_1m.iloc[-12], "kc_middle"),
    }
    if any(value is None for value in values.values()):
        return {"allowed": False, "reason": "BLOCKED_ENTRY_INDICATOR_DATA_INVALID"}
    if (
        values["ltf_atr"] <= 0
        or values["htf_atr"] <= 0
        or values["ltf_upper"] <= values["ltf_middle"]
        or values["ltf_middle"] <= values["ltf_lower"]
    ):
        return {"allowed": False, "reason": "BLOCKED_ENTRY_CHANNEL_OR_ATR_INVALID"}

    live_close = _finite(live, "close")
    if live_close is None or live_close <= 0:
        return {"allowed": False, "reason": "BLOCKED_ENTRY_LIVE_CLOSE_INVALID"}
    # Reprice live SMA values from the actual submission quote, not the
    # potentially older quote used when the market frame was assembled.
    values["ma5_current"] += (quote - live_close) / 5.0
    values["ma15"] += (quote - live_close) / 15.0

    htf_norm_slope = (
        values["htf_middle_current"] - values["htf_middle_previous"]
    ) / values["htf_atr"]
    ltf_short_norm = (
        values["ltf_middle"] - values["short_middle"]
    ) / values["ltf_atr"]
    ltf_long_norm = (
        values["ltf_middle"] - values["long_middle"]
    ) / values["ltf_atr"]
    evidence = {
        "htf_norm_slope": htf_norm_slope,
        "ltf_short_norm": ltf_short_norm,
        "ltf_long_norm": ltf_long_norm,
        "quote": quote,
        "current_open": values["open"],
        "kc_middle": values["ltf_middle"],
        "kc_upper": values["ltf_upper"],
        "kc_lower": values["ltf_lower"],
        "ma5_current": values["ma5_current"],
        "ma5_previous": values["ma5_previous"],
        "ma15": values["ma15"],
    }
    _logger.info(
        "[雙週期斜率] side=%s htf_norm_slope=%.3f short_slope=%.3f "
        "long_slope=%.3f",
        side, htf_norm_slope, ltf_short_norm, ltf_long_norm,
    )

    expected_sign = 1 if side == "LONG" else -1
    # The first impulse is allowed to lead the slow 15m/long-lookback slopes.
    # Still reject a clearly opposing macro slope or an opposing short slope.
    breaks_recent_high = quote > max(
        _finite(frame_1m.iloc[-2], "high") or quote,
        _finite(frame_1m.iloc[-3], "high") or quote,
    )
    breaks_recent_low = quote < min(
        _finite(frame_1m.iloc[-2], "low") or quote,
        _finite(frame_1m.iloc[-3], "low") or quote,
    )
    early_impulse = (
        (side == "LONG" and breaks_recent_high)
        or (side == "SHORT" and breaks_recent_low)
    ) and abs(quote - values["open"]) >= 0.25 * values["ltf_atr"]
    if abs(htf_norm_slope) <= HTF_SLOPE_THRESHOLD and not early_impulse:
        return {
            "allowed": False, "reason": "BLOCKED_HTF_FLAT",
            "evidence": evidence,
        }
    if (
        abs(ltf_short_norm) < LTF_SHORT_SLOPE_MIN
        or (not early_impulse and abs(ltf_long_norm) < LTF_LONG_SLOPE_MIN)
    ):
        _logger.warning(
            "[攔截開單 | 1m 中軌走平] short=%.3f, long=%.3f",
            ltf_short_norm, ltf_long_norm,
        )
        return {
            "allowed": False, "reason": "BLOCKED_LTF_FLAT",
            "evidence": evidence,
        }

    if (
        htf_norm_slope * expected_sign < -(0.25 if early_impulse else HTF_SLOPE_THRESHOLD)
        or ltf_short_norm * expected_sign < LTF_SHORT_SLOPE_MIN
        or (not early_impulse and ltf_long_norm * expected_sign < LTF_LONG_SLOPE_MIN)
    ):
        return {
            "allowed": False, "reason": "BLOCKED_ENTRY_TREND_SLOPE_MISMATCH",
            "evidence": evidence,
        }

    if side == "LONG":
        if not (
            values["ma5_current"] > values["ma5_previous"]
            and (early_impulse or values["ma5_current"] > values["ma15"])
        ):
            return {
                "allowed": False, "reason": "BLOCKED_ENTRY_MA_ALIGNMENT",
                "evidence": evidence,
            }
        if not early_impulse and not (
            values["ltf_upper"] > values["upper_previous_2"]
            and (
                values["ltf_lower"] > values["lower_previous_2"]
                or values["ltf_middle"] > values["short_middle"]
            )
        ):
            return {
                "allowed": False, "reason": "BLOCKED_ENTRY_KC_BANDS_NOT_RISING",
                "evidence": evidence,
            }
        if quote <= values["ltf_middle"]:
            return {
                "allowed": False, "reason": "BLOCKED_ENTRY_PRICE_NOT_ABOVE_MIDDLE",
                "evidence": evidence,
            }
        max_distance = MAX_ENTRY_BAND_RATIO * (
            values["ltf_middle"] - values["ltf_lower"]
        )
        if quote - values["ltf_middle"] > max_distance:
            return {
                "allowed": False, "reason": "BLOCKED_ENTRY_TOO_FAR_ABOVE_MIDDLE",
                "evidence": {**evidence, "max_midline_distance": max_distance},
            }
        candle_color_ok = quote > values["open"]
    else:
        if not (
            values["ma5_current"] < values["ma5_previous"]
            and (early_impulse or values["ma5_current"] < values["ma15"])
        ):
            return {
                "allowed": False, "reason": "BLOCKED_ENTRY_MA_ALIGNMENT",
                "evidence": evidence,
            }
        if not early_impulse and not (
            values["ltf_lower"] < values["lower_previous_2"]
            and (
                values["ltf_upper"] < values["upper_previous_2"]
                or values["ltf_middle"] < values["short_middle"]
            )
        ):
            return {
                "allowed": False, "reason": "BLOCKED_ENTRY_KC_BANDS_NOT_FALLING",
                "evidence": evidence,
            }
        if quote >= values["ltf_middle"]:
            return {
                "allowed": False, "reason": "BLOCKED_ENTRY_PRICE_NOT_BELOW_MIDDLE",
                "evidence": evidence,
            }
        max_distance = MAX_ENTRY_BAND_RATIO * (
            values["ltf_middle"] - values["ltf_lower"]
        )
        if values["ltf_middle"] - quote > max_distance:
            return {
                "allowed": False, "reason": "BLOCKED_ENTRY_TOO_FAR_BELOW_MIDDLE",
                "evidence": {**evidence, "max_midline_distance": max_distance},
            }
        candle_color_ok = quote < values["open"]

    if not candle_color_ok:
        reason = "BLOCKED_ENTRY_LIVE_CANDLE_COLOR"
        _logger.warning(
            "[防呆攔截 | Pre-Order Guard] side=%s quote=%.8f open=%.8f",
            side, quote, values["open"],
        )
        return {"allowed": False, "reason": reason, "evidence": evidence}

    live_bar = {
        field: _finite(live, field)
        for field in ("open", "high", "low")
    }
    if any(value is None for value in live_bar.values()):
        return {"allowed": False, "reason": "BLOCKED_ENTRY_LIVE_OHLC_INVALID"}
    live_bar["close"] = quote
    live_bar["high"] = max(live_bar["high"], quote)
    live_bar["low"] = min(live_bar["low"], quote)
    macro_indicators = {
        "kc_upper": values["ltf_upper"],
        "kc_middle": values["ltf_middle"],
        "kc_lower": values["ltf_lower"],
        "ma15": values["ma15"],
        "atr": values["ltf_atr"],
    }
    from core.gates.macro_candle_structure_gate import (
        MTF_KC_DIRECTIONAL_ENTRY,
        MacroCandleStructureGate,
    )

    macro_allowed, macro_reason = MacroCandleStructureGate().can_open(
        side,
        live_bar,
        macro_indicators,
        symbol=symbol,
        entry_pattern="CONTINUATION",
        structure_confirmation=MTF_KC_DIRECTIONAL_ENTRY,
        structure_confirmed=True,
        ignore_directional_entry_kc_location=True,
    )
    if not macro_allowed:
        return {
            "allowed": False,
            "reason": "BLOCKED_MACRO_CANDLE_STRUCTURE:" + macro_reason,
            "evidence": evidence,
        }
    return {
        "allowed": True,
        "reason": "PASSED_DUAL_TIMEFRAME_ENTRY_GATE",
        "evidence": evidence,
    }


def check_kc_overall_structure(df: pd.DataFrame) -> str:
    """Classify closed KC bands as aligned up, aligned down, or blocked."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return KC_OVERALL_CHOP_BOX

    closed = closed_entry_candles(df)
    required = ("kc_upper", "kc_middle", "kc_lower", "atr")
    if len(closed) < 8 or not all(column in closed.columns for column in required):
        return KC_OVERALL_CHOP_BOX

    values = closed.loc[:, required].apply(pd.to_numeric, errors="coerce")
    recent = values.tail(8)
    if (
        not np.isfinite(recent.to_numpy(dtype=float)).all()
        or (recent["atr"] <= 0).any()
        or (recent["kc_upper"] <= recent["kc_middle"]).any()
        or (recent["kc_middle"] <= recent["kc_lower"]).any()
    ):
        return KC_OVERALL_CHOP_BOX

    upper = recent["kc_upper"]
    middle = recent["kc_middle"]
    lower = recent["kc_lower"]
    atr = float(recent["atr"].iloc[-1])
    upper_range = float(upper.max() - upper.min())
    lower_range = float(lower.max() - lower.min())
    if upper_range < 0.1 * atr and lower_range < 0.1 * atr:
        return KC_OVERALL_CHOP_BOX

    upper_change = float(values["kc_upper"].iloc[-1] - values["kc_upper"].iloc[-3])
    middle_change = float(values["kc_middle"].iloc[-1] - values["kc_middle"].iloc[-3])
    lower_change = float(values["kc_lower"].iloc[-1] - values["kc_lower"].iloc[-3])
    if upper_change > 0 and middle_change > 0 and lower_change > 0:
        return KC_OVERALL_UP_ALIGNED
    if upper_change < 0 and middle_change < 0 and lower_change < 0:
        return KC_OVERALL_DOWN_ALIGNED
    return KC_OVERALL_CHOP_BOX


def kc_midline_slope_evidence(df: pd.DataFrame) -> dict[str, float] | None:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    closed = closed_entry_candles(df)
    if (
        len(closed) < LONG_SLOPE_LOOKBACK
        or "kc_middle" not in closed.columns
    ):
        return None
    middle = pd.to_numeric(closed["kc_middle"], errors="coerce")
    if not np.isfinite(middle.tail(LONG_SLOPE_LOOKBACK).to_numpy(dtype=float)).all():
        return None

    return {
        "short_slope": float(middle.iloc[-1] - middle.iloc[-3]),
        "long_slope": float(
            middle.iloc[-1] - middle.iloc[-LONG_SLOPE_LOOKBACK]
        ),
    }
