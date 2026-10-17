"""Closed-bar reversal and ATR ratchet exits for extreme Channel Swing shorts."""

from __future__ import annotations

import copy
import math
import time
from typing import Any, Dict, Optional, Tuple

STATE_KEY = "channel_short_oversold_exit_state"
TREND_HOLD_STATE_KEY = "channel_short_bearish_trend_hold_active"
REVERSAL_PARTIAL_TRIGGER = "CHANNEL_SHORT_OVERSOLD_REVERSAL_PARTIAL_50"
RATCHET_EXIT_TRIGGER = "CHANNEL_SHORT_OVERSOLD_RATCHET_EXIT"

EXTREME_DISTANCE_ATR = 0.5
REVERSAL_WICK_MIN_ATR = 0.3
RATCHET_REBOUND_ATR = 0.4


def _finite_positive(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _position_identity(position: Dict[str, Any]) -> Optional[str]:
    try:
        opened = float(position.get("open_timestamp") or 0.0)
        entry = float(position.get("entry_price") or 0.0)
        side = str(position.get("side") or "").upper()
        if opened >= 1e11:
            opened /= 1000.0
        if (side != "SHORT" or not math.isfinite(opened) or opened <= 0
                or not math.isfinite(entry) or entry <= 0):
            return None
        return f"{side}|{opened:.9f}|{entry:.12g}"
    except (TypeError, ValueError, OverflowError):
        return None


def _valid_bar(bar: Any) -> Optional[Tuple[float, float, float, float, float]]:
    if not isinstance(bar, dict):
        return None
    try:
        timestamp, opening, high, low, close = (
            float(bar.get(key) or 0.0)
            for key in ("ms", "o", "h", "l", "c")
        )
    except (TypeError, ValueError, OverflowError):
        return None
    if (not all(math.isfinite(value) and value > 0
                for value in (timestamp, opening, high, low, close))
            or high < max(opening, close)
            or low > min(opening, close)
            or low > high):
        return None
    return timestamp, opening, high, low, close


def _closed_reversal(history: Any) -> Optional[float]:
    if not isinstance(history, list) or len(history) < 2:
        return None
    previous = _valid_bar(history[-2])
    current = _valid_bar(history[-1])
    if previous is None or current is None:
        return None
    previous_ms, previous_open, _, _, previous_close = previous
    current_ms, current_open, _, current_low, current_close = current
    if current_ms <= previous_ms:
        return None

    body = abs(current_close - current_open)
    lower_wick = min(current_open, current_close) - current_low
    try:
        reference_atr = float(history[-2].get("atr") or 0.0)
    except (AttributeError, TypeError, ValueError, OverflowError):
        reference_atr = 0.0
    valid_atr = math.isfinite(reference_atr) and reference_atr > 0
    long_lower_wick = (
        valid_atr
        and lower_wick > 0
        and lower_wick >= body
        and lower_wick >= REVERSAL_WICK_MIN_ATR * reference_atr
    )
    bullish_engulfing = (
        previous_close < previous_open
        and current_close > current_open
        and current_open <= previous_close
        and current_close >= previous_open
    )
    return current_ms if long_lower_wick or bullish_engulfing else None


def short_bearish_trend_active(snapshot: Dict[str, Any]) -> Optional[bool]:
    """Require valid closed MA data before allowing an oversold short exit."""
    if not isinstance(snapshot, dict) or snapshot.get("reason") is not None:
        return None
    ma5_history = snapshot.get("ma5_history")
    ma15_history = snapshot.get("ma15_history")
    if (
        not isinstance(ma5_history, list)
        or not isinstance(ma15_history, list)
        or len(ma5_history) < 1
        or len(ma15_history) < 2
    ):
        return None
    try:
        ma5 = float(ma5_history[-1])
        previous_ma15 = float(ma15_history[-2])
        ma15 = float(ma15_history[-1])
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(
        math.isfinite(value) and value > 0
        for value in (ma5, previous_ma15, ma15)
    ):
        return None
    return ma5 < ma15 and ma15 < previous_ma15


def short_bearish_trend_in_frame(frame: Any) -> Optional[bool]:
    """Classify bearish MA alignment from fresh, completed one-minute candles."""
    if not isinstance(getattr(frame, "attrs", None), dict):
        return None
    if frame.attrs.get("timeframe_ms") != 60_000:
        return None
    try:
        from core.services.candle_data import closed_entry_candles

        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return None
        current, previous = closed.iloc[-1], closed.iloc[-2]
        ma5 = float(current["ma5"])
        ma15 = float(current["ma15"])
        previous_ma15 = float(previous["ma15"])
        timestamp = float(current["timestamp"])
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
        return None
    values = (ma5, ma15, previous_ma15, timestamp)
    if (
        not all(math.isfinite(value) and value > 0 for value in values)
        or timestamp < 1e11
        or time.time() * 1000 - timestamp > 300_000
    ):
        return None
    return ma5 < ma15 and ma15 < previous_ma15


def evaluate_short_oversold_exit(
    position: Dict[str, Any],
    metadata: Dict[str, Any],
    quote: float,
    snapshot: Dict[str, Any],
) -> Tuple[Optional[str], bool]:
    """Return a confirmed partial-reversal or trough-ratchet exit."""
    if (str(position.get("entry_mode") or metadata.get("entry_mode") or "").upper()
            != "CHANNEL_SWING"
            or str(position.get("side") or "").upper() != "SHORT"
            or not isinstance(snapshot, dict)
            or snapshot.get("reason") is not None):
        return None, False
    identity = _position_identity(position)
    atr = _finite_positive(snapshot.get("atr"))
    lower = _finite_positive(snapshot.get("live_kc_lower"))
    live_bar_ms = _finite_positive(snapshot.get("live_bar_ms"))
    closed_bar_ms = _finite_positive(snapshot.get("closed_bar_ms"))
    try:
        quote = float(quote)
    except (TypeError, ValueError, OverflowError):
        return None, False
    if (identity is None or atr is None or lower is None or live_bar_ms is None
            or closed_bar_ms is None or not math.isfinite(quote) or quote <= 0):
        return None, False

    existing = position.get(STATE_KEY) or metadata.get(STATE_KEY)
    state = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    changed = False
    if state.get("identity") != identity:
        if quote > lower - EXTREME_DISTANCE_ATR * atr:
            return None, False
        state = {
            "identity": identity,
            "armed": True,
            "atr": atr,
            "armed_bar_ms": live_bar_ms,
            "lowest_price": quote,
            "last_closed_bar_ms": closed_bar_ms,
            "partial_exit_done": False,
            "partial_exit_pending": False,
            "reversal_confirmed": False,
            "full_exit_pending": False,
        }
        changed = True

    bearish_trend = short_bearish_trend_active(snapshot)
    hold_for_bearish_trend = bearish_trend is not False
    if state.get("bearish_trend_hold_active") is not hold_for_bearish_trend:
        state["bearish_trend_hold_active"] = hold_for_bearish_trend
        changed = True
    if metadata.get(TREND_HOLD_STATE_KEY) is not hold_for_bearish_trend:
        metadata[TREND_HOLD_STATE_KEY] = hold_for_bearish_trend
        changed = True
    if hold_for_bearish_trend and state.get("armed"):
        lowest = _finite_positive(state.get("lowest_price"))
        observed_lows = [quote]
        live_low = _finite_positive(snapshot.get("live_low"))
        if live_low is not None:
            observed_lows.append(live_low)
        history = snapshot.get("history_5")
        if isinstance(history, list) and history:
            current_bar = _valid_bar(history[-1])
            armed_bar_ms = _finite_positive(state.get("armed_bar_ms")) or 0.0
            if current_bar is not None and current_bar[0] >= armed_bar_ms:
                observed_lows.append(current_bar[3])
                last_closed_ms = (
                    _finite_positive(state.get("last_closed_bar_ms")) or 0.0
                )
                if current_bar[0] > last_closed_ms:
                    state["last_closed_bar_ms"] = current_bar[0]
                    changed = True
        if lowest is not None:
            observed_low = min([lowest, *observed_lows])
            if observed_low < lowest:
                state["lowest_price"] = observed_low
                changed = True
        for key in (
            "partial_exit_pending",
            "reversal_confirmed",
            "full_exit_pending",
        ):
            if state.get(key):
                state[key] = False
                changed = True
        if state.pop("reversal_bar_ms", None) is not None:
            changed = True
        if state.pop("full_exit_price", None) is not None:
            changed = True
        position[STATE_KEY] = copy.deepcopy(state)
        metadata[STATE_KEY] = copy.deepcopy(state)
        return None, changed

    elif state.get("full_exit_pending"):
        position[STATE_KEY] = copy.deepcopy(state)
        metadata[STATE_KEY] = copy.deepcopy(state)
        return RATCHET_EXIT_TRIGGER, changed
    elif state.get("partial_exit_pending") or (
        state.get("reversal_confirmed") and not state.get("partial_exit_done")
    ):
        position[STATE_KEY] = copy.deepcopy(state)
        metadata[STATE_KEY] = copy.deepcopy(state)
        return REVERSAL_PARTIAL_TRIGGER, changed

    if not state.get("armed"):
        return None, changed

    atr = _finite_positive(state.get("atr"))
    lowest = _finite_positive(state.get("lowest_price"))
    if atr is None or lowest is None:
        return None, changed
    observed_lows = [quote]
    live_low = _finite_positive(snapshot.get("live_low"))
    if live_low is not None:
        observed_lows.append(live_low)
    history = snapshot.get("history_5")
    if isinstance(history, list) and history:
        current_bar = _valid_bar(history[-1])
        armed_bar_ms = _finite_positive(state.get("armed_bar_ms")) or 0.0
        if current_bar is not None and current_bar[0] >= armed_bar_ms:
            observed_lows.append(current_bar[3])
    observed_low = min(observed_lows)
    if observed_low < lowest:
        state["lowest_price"] = observed_low
        lowest = observed_low
        changed = True

    rebound_trigger = lowest + RATCHET_REBOUND_ATR * atr
    if quote >= rebound_trigger:
        state["full_exit_pending"] = True
        state["full_exit_price"] = rebound_trigger
        position[STATE_KEY] = copy.deepcopy(state)
        metadata[STATE_KEY] = copy.deepcopy(state)
        return RATCHET_EXIT_TRIGGER, True

    reversal_bar_ms = _closed_reversal(history)
    last_closed_ms = _finite_positive(state.get("last_closed_bar_ms")) or 0.0
    armed_bar_ms = _finite_positive(state.get("armed_bar_ms")) or 0.0
    if isinstance(history, list) and history:
        current_bar = _valid_bar(history[-1])
        if current_bar is not None and current_bar[0] > last_closed_ms:
            state["last_closed_bar_ms"] = current_bar[0]
            changed = True
            if (reversal_bar_ms is not None
                    and reversal_bar_ms == current_bar[0]
                    and reversal_bar_ms >= armed_bar_ms
                    and not state.get("partial_exit_done")):
                state["reversal_confirmed"] = True
                state["reversal_bar_ms"] = reversal_bar_ms
                state["partial_exit_pending"] = True
                changed = True

    position[STATE_KEY] = copy.deepcopy(state)
    metadata[STATE_KEY] = copy.deepcopy(state)
    if state.get("reversal_confirmed") and not state.get("partial_exit_done"):
        return REVERSAL_PARTIAL_TRIGGER, changed
    return None, changed


def mark_partial_exit_result(
    position: Dict[str, Any],
    metadata: Dict[str, Any],
    *,
    completed: bool,
    quarantined: bool = False,
) -> None:
    """Persist the one-shot reversal reduction result or fail-closed quarantine."""
    state = position.get(STATE_KEY) or metadata.get(STATE_KEY)
    if not isinstance(state, dict):
        return
    state = copy.deepcopy(state)
    state["partial_exit_pending"] = bool(quarantined)
    if completed:
        state["partial_exit_done"] = True
        state["partial_exit_pending"] = False
    position[STATE_KEY] = copy.deepcopy(state)
    metadata[STATE_KEY] = copy.deepcopy(state)


def mark_full_exit_pending(position: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    state = position.get(STATE_KEY) or metadata.get(STATE_KEY)
    if not isinstance(state, dict):
        return
    state = copy.deepcopy(state)
    state["full_exit_pending"] = True
    position[STATE_KEY] = copy.deepcopy(state)
    metadata[STATE_KEY] = copy.deepcopy(state)
