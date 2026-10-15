"""Shared closed-candle gate for trend pullback entries."""

import math
from typing import Any, Optional

import numpy as np
import pandas as pd

from core.services.candle_data import closed_entry_candles


SMART_ENTRY_CODES = {
    "LONG": "SMART_ENTRY_PULLBACK_LONG",
    "SHORT": "SMART_ENTRY_PULLBACK_SHORT",
}
MIN_CLOSED_BARS = 31
WHIPSAW_LOOKBACK = 10
RANGE_LOOKBACK = 15
RANGE_BREAKOUT_VOLUME_RATIO = 1.5
COOLDOWN_BARS = 5


def _middle_column(frame: pd.DataFrame) -> Optional[str]:
    for column in ("kc_middle", "spec_kc_middle", "ema_20", "ma15"):
        if column in frame.columns:
            return column
    return None


def _cooldown_problem(frame: pd.DataFrame, account: Any, symbol: str) -> Optional[str]:
    if account is None or not symbol:
        return None
    closed_at = getattr(account, "last_closed_at", {}).get(symbol)
    if closed_at is None:
        return None
    try:
        closed_at_ms = float(closed_at) * 1000.0
        if closed_at_ms > 1e14:
            closed_at_ms /= 1000.0
        timestamps = pd.to_numeric(frame["timestamp"], errors="coerce")
        interval = float(frame.attrs.get("timeframe_ms") or 60_000.0)
        if not math.isfinite(closed_at_ms) or closed_at_ms <= 0:
            return "BLOCKED_SMART_ENTRY_COOLDOWN_DATA_INVALID"
        if (
            timestamps.isna().any()
            or not np.isfinite(timestamps.to_numpy(dtype=float)).all()
            or not math.isfinite(interval)
            or interval <= 0
        ):
            return "BLOCKED_SMART_ENTRY_COOLDOWN_DATA_INVALID"
        if float(timestamps.iloc[-1]) < 1e11:
            timestamps = timestamps * 1000.0
        closed_bars_after_exit = int((timestamps + interval > closed_at_ms).sum())
        if closed_bars_after_exit < COOLDOWN_BARS:
            return (
                "BLOCKED_SMART_ENTRY_COOLDOWN "
                f"({closed_bars_after_exit}/{COOLDOWN_BARS} closed bars)"
            )
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return "BLOCKED_SMART_ENTRY_COOLDOWN_DATA_INVALID"
    return None


def smart_entry_gate_problem(
    frame: pd.DataFrame,
    side: str,
    *,
    account: Any = None,
    symbol: str = "",
    quote: Optional[float] = None,
) -> tuple[Optional[str], Optional[float]]:
    """Return a fail-closed reason and the qualifying range-break bar id."""
    side = str(side).upper()
    if side not in ("LONG", "SHORT") or frame is None or frame.empty:
        return "BLOCKED_SMART_ENTRY_INVALID_DATA", None

    closed = closed_entry_candles(frame)
    middle_col = _middle_column(closed)
    short_ma_col = "ma5" if "ma5" in closed.columns else "ma3"
    required = {
        "timestamp", "open", "high", "low", "close", "volume",
        "kc_upper", "kc_lower",
    }
    if (
        len(closed) < MIN_CLOSED_BARS
        or not required.issubset(closed.columns)
        or middle_col is None
        or short_ma_col not in closed.columns
    ):
        return "BLOCKED_SMART_ENTRY_INSUFFICIENT_OR_INVALID_DATA", None

    cooldown_problem = _cooldown_problem(closed, account, symbol)
    if cooldown_problem:
        return cooldown_problem, None

    columns = [
        "open", "high", "low", "close", "volume", "kc_upper", "kc_lower",
        middle_col, short_ma_col,
    ]
    recent = closed.tail(max(MIN_CLOSED_BARS, WHIPSAW_LOOKBACK + 1)).copy()
    try:
        values = recent[columns].apply(pd.to_numeric, errors="coerce")
        if values.isna().any().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            return "BLOCKED_SMART_ENTRY_INVALID_DATA", None
        if (
            (values[["open", "high", "low", "close", "kc_upper", "kc_lower", middle_col, short_ma_col]] <= 0)
            .any().any()
            or (values["volume"] < 0).any()
            or (values["kc_lower"] >= values["kc_upper"]).any()
            or (values["high"] < values[["open", "close", "low"]].max(axis=1)).any()
            or (values["low"] > values[["open", "close", "high"]].min(axis=1)).any()
        ):
            return "BLOCKED_SMART_ENTRY_INVALID_DATA", None
    except (KeyError, TypeError, ValueError):
        return "BLOCKED_SMART_ENTRY_INVALID_DATA", None

    bars = closed.copy()
    bars[columns] = bars[columns].apply(pd.to_numeric, errors="coerce")
    tail = bars.tail(WHIPSAW_LOOKBACK)
    cross_count = 0
    last_side = 0
    for _, row in tail.iterrows():
        relative = float(row["close"]) - float(row[middle_col])
        current_side = 1 if relative > 0 else -1 if relative < 0 else 0
        if current_side and last_side and current_side != last_side:
            cross_count += 1
        if current_side:
            last_side = current_side
    if cross_count >= 2:
        return f"BLOCKED_SMART_ENTRY_CHOP_CROSSES ({cross_count})", None

    current = bars.iloc[-1]
    previous = bars.iloc[-2]
    middle = float(current[middle_col])
    previous_middle = float(previous[middle_col])
    if side == "LONG" and middle <= previous_middle:
        return "BLOCKED_SMART_ENTRY_KC_TREND_NOT_RISING", None
    if side == "SHORT" and middle >= previous_middle:
        return "BLOCKED_SMART_ENTRY_KC_TREND_NOT_FALLING", None

    lower, upper = float(current["kc_lower"]), float(current["kc_upper"])
    channel_width = upper - lower
    close = float(current["close"])
    kc_pos = (close - lower) / channel_width
    if side == "LONG":
        if kc_pos > 0.80 or close >= upper:
            return "BLOCKED_SMART_ENTRY_LONG_OUTER_CHASE", None
        if not 0.40 <= kc_pos <= 0.70:
            return "BLOCKED_SMART_ENTRY_LONG_KC_POSITION", None
        if not (float(current["close"]) > float(current["open"])):
            return "BLOCKED_SMART_ENTRY_LONG_CONFIRMATION_CANDLE", None
        if float(previous["close"]) <= float(previous[middle_col]):
            return "BLOCKED_SMART_ENTRY_LONG_NOT_ABOVE_MIDLINE", None
    else:
        if kc_pos < 0.20 or close <= lower:
            return "BLOCKED_SMART_ENTRY_SHORT_OUTER_CHASE", None
        if not 0.30 <= kc_pos <= 0.60:
            return "BLOCKED_SMART_ENTRY_SHORT_KC_POSITION", None
        if not (float(current["close"]) < float(current["open"])):
            return "BLOCKED_SMART_ENTRY_SHORT_CONFIRMATION_CANDLE", None
        if float(previous["close"]) >= float(previous[middle_col]):
            return "BLOCKED_SMART_ENTRY_SHORT_NOT_BELOW_MIDLINE", None

    if quote is not None:
        try:
            execution_quote = float(quote)
        except (TypeError, ValueError, OverflowError):
            return "BLOCKED_SMART_ENTRY_INVALID_QUOTE", None
        if not math.isfinite(execution_quote) or execution_quote <= 0:
            return "BLOCKED_SMART_ENTRY_INVALID_QUOTE", None
        quote_pos = (execution_quote - lower) / channel_width
        if side == "LONG" and (
            execution_quote >= upper or quote_pos > 0.80
            or not 0.40 <= quote_pos <= 0.70
        ):
            return "BLOCKED_SMART_ENTRY_QUOTE_OUT_OF_RANGE", None
        if side == "SHORT" and (
            execution_quote <= lower or quote_pos < 0.20
            or not 0.30 <= quote_pos <= 0.60
        ):
            return "BLOCKED_SMART_ENTRY_QUOTE_OUT_OF_RANGE", None

    short_ma = float(current[short_ma_col])
    zone_low, zone_high = sorted((middle, short_ma))
    touches_retest = (
        float(current["low"]) <= zone_high
        and float(current["high"]) >= zone_low
    )
    if side == "LONG":
        confirmed_retest = touches_retest and close > zone_high
    else:
        confirmed_retest = touches_retest and close < zone_low
    if not confirmed_retest:
        return "BLOCKED_SMART_ENTRY_PULLBACK_NOT_CONFIRMED", None

    range_bars = bars.iloc[-RANGE_LOOKBACK - 1:-1]
    range_high = float(range_bars["high"].max())
    range_low = float(range_bars["low"].min())
    range_width = range_high - range_low
    if not math.isfinite(range_width) or range_width <= 0:
        return "BLOCKED_SMART_ENTRY_INVALID_RANGE", None
    range_pos = (close - range_low) / range_width
    inside_range = 0.20 <= range_pos <= 0.80

    breakout_events = []
    first_candidate = max(RANGE_LOOKBACK, len(bars) - RANGE_LOOKBACK - 1)
    for index in range(first_candidate, len(bars) - 1):
        candidate = bars.iloc[index]
        prior_range = bars.iloc[index - RANGE_LOOKBACK:index]
        prior_high = float(prior_range["high"].max())
        prior_low = float(prior_range["low"].min())
        average_volume = float(prior_range["volume"].mean())
        if average_volume <= 0:
            continue
        body_up = (
            float(candidate["close"]) > prior_high
            and float(candidate["open"]) <= prior_high
            and float(candidate["close"]) > float(candidate["open"])
        )
        body_down = (
            float(candidate["close"]) < prior_low
            and float(candidate["open"]) >= prior_low
            and float(candidate["close"]) < float(candidate["open"])
        )
        if (
            body_up and float(candidate["volume"]) >= RANGE_BREAKOUT_VOLUME_RATIO * average_volume
        ):
            breakout_events.append((index, "LONG"))
        elif (
            body_down and float(candidate["volume"]) >= RANGE_BREAKOUT_VOLUME_RATIO * average_volume
        ):
            breakout_events.append((index, "SHORT"))

    matching_breakout_id = None
    matching_breakout_index = None
    if breakout_events:
        index, event_side = breakout_events[-1]
        if event_side == side:
            matching_breakout_id = float(bars.iloc[index]["timestamp"])
            matching_breakout_index = index
    if inside_range and matching_breakout_id is None:
        return "BLOCKED_SMART_ENTRY_INSIDE_RANGE", None

    if inside_range:
        if matching_breakout_index is None or matching_breakout_index >= len(bars) - 1:
            return "BLOCKED_SMART_ENTRY_RANGE_BREAKOUT_NOT_RETESTED", None

    return None, matching_breakout_id


def smart_entry_candidate(
    frame: pd.DataFrame,
    side: str,
    *,
    account: Any = None,
    symbol: str = "",
    quote: Optional[float] = None,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    problem, breakout_bar_id = smart_entry_gate_problem(
        frame, side, account=account, symbol=symbol, quote=quote,
    )
    if problem:
        return None, problem
    closed = closed_entry_candles(frame)
    current = closed.iloc[-1]
    stamp = float(current["timestamp"])
    side = str(side).upper()
    code = SMART_ENTRY_CODES[side]
    return {
        "action": "ENTER",
        "type": code,
        "side": side,
        "reason": "SMART_TREND_PULLBACK_RETEST",
        "price": float(quote) if quote is not None else float(current["close"]),
        "close_price": float(current["close"]),
        "entry_atr": float(current.get("atr", 0.0) or 0.0),
        "confirmation_bar_id": stamp,
        "breakout_bar_id": breakout_bar_id or stamp,
        "pair_confirmation_bar_id": stamp,
        "entry_phase": "SMART_ENTRY_PULLBACK",
        "pending_signal_id": f"{symbol}:{code}:{int(stamp)}",
    }, None
