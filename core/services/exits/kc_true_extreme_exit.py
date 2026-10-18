"""KC outer-band exhaustion and midline exits for automated positions."""

from __future__ import annotations

import math
from typing import Any, Optional

import pandas as pd

from core.services.candle_data import closed_entry_candles

STATE_KEY = "kc_true_extreme_exit_state"
_STATE_VERSION = 1
_EXIT_REASONS = {
    "LONG": "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL",
    "SHORT": "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL",
}
_MIDLINE_REASON = "EXIT_BY_KC_MIDLINE_RETRACE"


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _position_identity(position: dict[str, Any]) -> Optional[list[Any]]:
    side = str(position.get("side") or "").upper()
    if side not in _EXIT_REASONS:
        return None
    opened_value = next(
        (
            position.get(key)
            for key in (
                "open_timestamp",
                "entry_timestamp",
                "open_bar_id",
                "entry_bar_id",
            )
            if position.get(key) is not None
        ),
        None,
    )
    entry = _number(position.get("entry_price"))
    quantity = _number(
        position.get("qty") or position.get("quantity") or position.get("amount")
    )
    opened = _number(opened_value)
    if opened is None or entry is None or quantity is None:
        return None
    if opened >= 1e11:
        opened /= 1000.0
    if opened <= 0 or entry <= 0 or quantity <= 0:
        return None
    return [side, opened, entry, abs(quantity)]


def _valid_ohlc(row: pd.Series) -> Optional[tuple[float, float, float, float]]:
    values = tuple(_number(row.get(key)) for key in ("open", "high", "low", "close"))
    if any(value is None or value <= 0 for value in values):
        return None
    opened, high, low, close = values
    if high < max(opened, close) or low > min(opened, close) or low > high:
        return None
    return opened, high, low, close


def short_kc_bearish_trend_active(
    frame: Optional[pd.DataFrame],
    quote_ms: float,
) -> bool:
    if (
        frame is None
        or frame.empty
        or frame.attrs.get("timeframe_ms", 60_000) != 60_000
    ):
        return False
    stamp = _number(quote_ms)
    if stamp is None or stamp <= 0:
        return False
    snapshot_ms = _number(frame.attrs.get("snapshot_ms"))
    if snapshot_ms is not None and (
        stamp < snapshot_ms - 5_000 or stamp - snapshot_ms > 300_000
    ):
        return False
    try:
        live = frame.iloc[-1]
        live_bar_ms = _number(live.get("timestamp"))
        closed = closed_entry_candles(frame)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return False
    is_live = live.get("is_closed") is False or (
        type(live.get("is_closed")).__name__ == "bool_"
        and not bool(live.get("is_closed"))
    )
    if (
        live_bar_ms is None
        or len(closed) < 2
        or not is_live
        or live_bar_ms != math.floor(stamp / 60_000) * 60_000
        or _number(closed.iloc[-1].get("timestamp")) != live_bar_ms - 60_000
    ):
        return False
    previous_middle = _number(closed.iloc[-2].get("kc_middle"))
    latest_middle = _number(closed.iloc[-1].get("kc_middle"))
    return (
        previous_middle is not None
        and latest_middle is not None
        and previous_middle > 0
        and latest_middle > 0
        and latest_middle < previous_middle
    )


def _state_for(
    position: dict[str, Any],
    metadata: dict[str, Any],
    identity: list[Any],
) -> tuple[dict[str, Any], bool]:
    previous = position.get(STATE_KEY)
    if not isinstance(previous, dict):
        previous = metadata.get(STATE_KEY)
    if (
        isinstance(previous, dict)
        and previous.get("version") == _STATE_VERSION
        and previous.get("identity") == identity
    ):
        state = dict(previous)
        return state, False
    return {
        "version": _STATE_VERSION,
        "identity": identity,
        "last_closed_bar_ms": None,
        "armed": False,
        "peak_or_trough": None,
        "reference_atr": None,
        "outer_extension_atr": None,
        "pending": None,
    }, True


def pending_kc_true_extreme_exit(
    position: dict[str, Any],
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[tuple[str, dict[str, Any]]]:
    identity = _position_identity(position)
    if identity is None:
        return None
    sources = (position, metadata) if isinstance(metadata, dict) else (position,)
    for source in sources:
        state = source.get(STATE_KEY)
        if (
            not isinstance(state, dict)
            or state.get("version") != _STATE_VERSION
            or state.get("identity") != identity
        ):
            continue
        pending = state.get("pending")
        if (
            not isinstance(pending, dict)
            or pending.get("reason")
            not in (_EXIT_REASONS[identity[0]], _MIDLINE_REASON)
            or not isinstance(pending.get("details"), dict)
        ):
            continue
        return pending["reason"], dict(pending["details"])
    return None


def _mirror_state(
    position: dict[str, Any],
    metadata: dict[str, Any],
    state: dict[str, Any],
) -> None:
    position[STATE_KEY] = dict(state)
    metadata[STATE_KEY] = dict(state)


def _set_pending(
    side: str,
    state: dict[str, Any],
    reason: str,
    details: dict[str, Any],
) -> None:
    state["pending"] = {
        "reason": reason,
        "details": details,
    }


def evaluate_kc_true_extreme_exit(
    position: dict[str, Any],
    frame: Optional[pd.DataFrame],
    price: float,
    quote_ms: float,
    metadata: Optional[dict[str, Any]] = None,
) -> tuple[Optional[tuple[str, dict[str, Any]]], bool]:
    """Return a pending or newly confirmed extreme/midline exit and state changes."""
    if not isinstance(position, dict):
        return None, False
    meta = metadata if isinstance(metadata, dict) else {}
    identity = _position_identity(position)
    if identity is None:
        return None, False
    side = identity[0]
    previous_state = position.get(STATE_KEY) or meta.get(STATE_KEY)
    state, state_changed = _state_for(position, meta, identity)

    retired_pivot = False
    for source in (position, meta):
        retired_pivot = (
            source.pop("confirmed_true_pivot_exit_pending", None) is not None
            or retired_pivot
        )
    if retired_pivot:
        state_changed = True

    pending = pending_kc_true_extreme_exit(position, meta)
    if pending is not None:
        bearish_short_exit_exception = (
            side == "SHORT"
            and pending[0] == _EXIT_REASONS["SHORT"]
            and pending[1].get("cause") in (
                "BULLISH_ENGULFING",
                "INTRADAY_TROUGH_REBOUND",
            )
        )
        if (
            side == "SHORT"
            and short_kc_bearish_trend_active(frame, quote_ms)
            and not bearish_short_exit_exception
        ):
            state["pending"] = None
            state_changed = True
            _mirror_state(position, meta, state)
        else:
            if state_changed:
                _mirror_state(position, meta, state)
            return pending, state_changed

    current_price = _number(price)
    stamp = _number(quote_ms)
    if (
        frame is None
        or frame.empty
        or frame.attrs.get("timeframe_ms", 60_000) != 60_000
        or current_price is None
        or current_price <= 0
        or stamp is None
        or stamp <= 0
    ):
        if state_changed:
            _mirror_state(position, meta, state)
        return None, state_changed

    try:
        snapshot_ms = _number(frame.attrs.get("snapshot_ms"))
        if snapshot_ms is not None and (
            stamp < snapshot_ms - 5_000 or stamp - snapshot_ms > 300_000
        ):
            if state_changed:
                _mirror_state(position, meta, state)
            return None, state_changed
        live = frame.iloc[-1]
        live_bar_ms = _number(live.get("timestamp"))
        closed = closed_entry_candles(frame)
        if (
            live_bar_ms is None
            or len(closed) < 2
            or not (
                live.get("is_closed") is False
                or (
                    type(live.get("is_closed")).__name__ == "bool_"
                    and not bool(live.get("is_closed"))
                )
            )
            or live_bar_ms != math.floor(stamp / 60_000) * 60_000
            or float(closed.iloc[-1].get("timestamp")) != live_bar_ms - 60_000
        ):
            if state_changed:
                _mirror_state(position, meta, state)
            return None, state_changed
        live_ohlc = _valid_ohlc(live)
        closed_ohlc = _valid_ohlc(closed.iloc[-1])
        previous_ohlc = _valid_ohlc(closed.iloc[-2])
        if live_ohlc is None or closed_ohlc is None or previous_ohlc is None:
            if state_changed:
                _mirror_state(position, meta, state)
            return None, state_changed
        live_middle = _number(live.get("kc_middle"))
        live_upper = _number(live.get("kc_upper"))
        live_lower = _number(live.get("kc_lower"))
        closed_atr = _number(closed.iloc[-1].get("atr"))
        previous_atr = _number(closed.iloc[-2].get("atr"))
        bearish_kc_trend = (
            side == "SHORT"
            and short_kc_bearish_trend_active(frame, stamp)
        )
        if live_middle is None:
            if state_changed:
                _mirror_state(position, meta, state)
            return None, state_changed

        entry_ms = identity[1] * 1000.0
        entry_bar_ms = math.floor(entry_ms / 60_000) * 60_000
        from core.services.exits.strict_true_pivot import confirmed_true_pivot_exit

        confirmed_pivot = confirmed_true_pivot_exit(position, frame)
        if confirmed_pivot is not None and not bearish_kc_trend:
            pivot_reason, pivot_details = confirmed_pivot
            exit_reason = _EXIT_REASONS[side]
            details = {
                **pivot_details,
                "cause": "THREE_POINT_CONFIRMED_PIVOT",
                "bar_ms": pivot_details["confirm_bar_id"],
                "confirmed_pivot_reason": pivot_reason,
            }
            _set_pending(side, state, exit_reason, details)
            _mirror_state(position, meta, state)
            return (exit_reason, details), True

        closed_bar_ms = _number(closed.iloc[-1].get("timestamp"))
        closed_signal = None
        if (
            closed_bar_ms is not None
            and closed_bar_ms > entry_bar_ms
            and state.get("last_closed_bar_ms") != closed_bar_ms
        ):
            opened, high, low, close = closed_ohlc
            prior_open, _, _, prior_close = previous_ohlc
            prior_atr = previous_atr
            if side == "LONG":
                body = abs(close - opened)
                upper_wick = high - max(opened, close)
                upper_band = _number(closed.iloc[-1].get("kc_upper"))
                reached = (
                    previous_atr is not None
                    and previous_atr > 0
                    and upper_band is not None
                    and upper_band > 0
                    and high - upper_band >= 0.5 * prior_atr
                )
                extension_atr = (
                    (high - upper_band) / prior_atr
                    if upper_band is not None and prior_atr is not None
                    and prior_atr > 0 else None
                )
                engulfed = (
                    close < opened
                    and prior_close > prior_open
                    and opened >= prior_close
                    and close <= prior_open
                )
                if reached:
                    prior_extension = _number(state.get("outer_extension_atr"))
                    state.update(
                        armed=True,
                        peak_or_trough=max(
                            float(state.get("peak_or_trough") or high), high,
                        ),
                        reference_atr=float(state.get("reference_atr") or prior_atr),
                        outer_extension_atr=max(
                            extension_atr or 0.0,
                            prior_extension or 0.0,
                        ),
                    )
                if state.get("armed"):
                    extension_detail = _number(state.get("outer_extension_atr"))
                    if extension_detail is None:
                        extension_detail = extension_atr
                    wick_rejection = (
                        prior_atr is not None
                        and prior_atr > 0
                        and upper_wick >= body
                        and upper_wick >= 0.3 * prior_atr
                    )
                    if wick_rejection:
                        closed_signal = (
                            _EXIT_REASONS[side],
                            {
                                "cause": "LONG_UPPER_WICK",
                                "bar_ms": closed_bar_ms,
                                "outer_extension_atr": extension_detail,
                            },
                        )
                    elif engulfed:
                        closed_signal = (
                            _EXIT_REASONS[side],
                            {
                                "cause": "BEARISH_ENGULFING",
                                "bar_ms": closed_bar_ms,
                                "outer_extension_atr": extension_detail,
                            },
                        )
            else:
                body = abs(close - opened)
                lower_wick = min(opened, close) - low
                lower_band = _number(closed.iloc[-1].get("kc_lower"))
                reached = (
                    previous_atr is not None
                    and previous_atr > 0
                    and lower_band is not None
                    and lower_band > 0
                    and low < lower_band
                )
                extension_atr = (
                    (lower_band - low) / prior_atr
                    if lower_band is not None and prior_atr is not None
                    and prior_atr > 0 else None
                )
                engulfed = (
                    close > opened
                    and prior_close < prior_open
                    and opened <= prior_close
                    and close >= prior_open
                )
                if reached:
                    prior_extension = _number(state.get("outer_extension_atr"))
                    state.update(
                        armed=True,
                        peak_or_trough=min(
                            float(state.get("peak_or_trough") or low), low,
                        ),
                        reference_atr=float(state.get("reference_atr") or prior_atr),
                        outer_extension_atr=max(
                            extension_atr or 0.0,
                            prior_extension or 0.0,
                        ),
                    )
                if state.get("armed"):
                    extension_detail = _number(state.get("outer_extension_atr"))
                    if extension_detail is None:
                        extension_detail = extension_atr
                    wick_rejection = (
                        prior_atr is not None
                        and prior_atr > 0
                        and lower_wick >= body
                        and lower_wick >= 0.3 * prior_atr
                    )
                    if wick_rejection:
                        closed_signal = (
                            _EXIT_REASONS[side],
                            {
                                "cause": "SHORT_LOWER_WICK",
                                "bar_ms": closed_bar_ms,
                                "outer_extension_atr": extension_detail,
                            },
                        )
                    elif engulfed:
                        closed_signal = (
                            _EXIT_REASONS[side],
                            {
                                "cause": "BULLISH_ENGULFING",
                                "bar_ms": closed_bar_ms,
                                "outer_extension_atr": extension_detail,
                            },
                        )
            state["last_closed_bar_ms"] = closed_bar_ms
            state_changed = True

        closed_signal_is_bullish_engulfing = (
            closed_signal is not None
            and side == "SHORT"
            and closed_signal[1].get("cause") == "BULLISH_ENGULFING"
        )
        if closed_signal is not None and (
            not bearish_kc_trend or closed_signal_is_bullish_engulfing
        ):
            reason, details = closed_signal
            _set_pending(side, state, reason, details)
        elif live_bar_ms > entry_bar_ms:
            _, live_high, live_low, _ = live_ohlc
            observed_high = max(live_high, current_price)
            observed_low = min(live_low, current_price)
            if side == "LONG":
                if (
                    not state.get("armed")
                    and live_upper is not None
                    and live_upper > live_middle
                    and closed_atr is not None
                    and closed_atr > 0
                    and observed_high - live_upper >= 0.5 * closed_atr
                ):
                    state.update(
                        armed=True,
                        peak_or_trough=observed_high,
                        reference_atr=closed_atr,
                        outer_extension_atr=(
                            observed_high - live_upper
                        ) / closed_atr,
                    )
                    state_changed = True
                if state.get("armed"):
                    extreme = max(
                        float(state.get("peak_or_trough") or observed_high),
                        observed_high,
                    )
                    if extreme != state.get("peak_or_trough"):
                        state["peak_or_trough"] = extreme
                        state_changed = True
                    reference_atr = _number(state.get("reference_atr"))
                    if (
                        reference_atr is not None
                        and extreme - current_price >= 0.4 * reference_atr
                    ):
                        _set_pending(
                            side,
                            state,
                            _EXIT_REASONS[side],
                            {
                                "cause": "INTRADAY_PEAK_PULLBACK",
                                "peak_price": extreme,
                                "quote_price": current_price,
                                "reference_atr": reference_atr,
                            },
                        )
            else:
                if (
                    not state.get("armed")
                    and live_lower is not None
                    and live_lower < live_middle
                    and closed_atr is not None
                    and closed_atr > 0
                    and observed_low < live_lower
                ):
                    state.update(
                        armed=True,
                        peak_or_trough=observed_low,
                        reference_atr=closed_atr,
                        outer_extension_atr=(
                            live_lower - observed_low
                        ) / closed_atr,
                    )
                    state_changed = True
                if state.get("armed"):
                    extreme = min(
                        float(state.get("peak_or_trough") or observed_low),
                        observed_low,
                    )
                    if extreme != state.get("peak_or_trough"):
                        state["peak_or_trough"] = extreme
                        state_changed = True
                    reference_atr = _number(state.get("reference_atr"))
                    if (
                        reference_atr is not None
                        and current_price - extreme >= 0.4 * reference_atr
                    ):
                        _set_pending(
                            side,
                            state,
                            _EXIT_REASONS[side],
                            {
                                "cause": "INTRADAY_TROUGH_REBOUND",
                                "trough_price": extreme,
                                "quote_price": current_price,
                                "reference_atr": reference_atr,
                            },
                        )

        if state.get("pending") is None:
            if side == "LONG" and current_price <= live_middle:
                _set_pending(
                    side,
                    state,
                    _MIDLINE_REASON,
                    {
                        "cause": "KC_MIDLINE_TOUCH",
                        "midline": live_middle,
                        "quote_price": current_price,
                    },
                )
            elif (
                side == "SHORT"
                and not bearish_kc_trend
                and current_price >= live_middle
            ):
                _set_pending(
                    side,
                    state,
                    _MIDLINE_REASON,
                    {
                        "cause": "KC_MIDLINE_TOUCH",
                        "midline": live_middle,
                        "quote_price": current_price,
                    },
                )

        state_changed = state_changed or previous_state != state
        _mirror_state(position, meta, state)
        pending = state.get("pending")
        if isinstance(pending, dict):
            return (pending["reason"], dict(pending["details"])), state_changed
        return None, state_changed
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        if state_changed:
            _mirror_state(position, meta, state)
        return None, state_changed
