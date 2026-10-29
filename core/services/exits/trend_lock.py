"""Closed-candle strategy exit lock and persistent trend-cycle entry lock."""

from __future__ import annotations

import math
from typing import Any, Mapping

from core.services.candle_data import closed_entry_candles

MAX_TREND_LOCK_STALENESS_MS = 180_000.0


def _value(row: Any, key: str) -> float | None:
    try:
        value = float(row.get(key))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _entry_bars(position: Mapping[str, Any], frame: Any):
    closed = closed_entry_candles(frame)
    if closed.empty:
        return closed
    opened = _value(position, "open_timestamp") or _value(position, "entry_timestamp")
    if opened and "timestamp" in closed:
        opened_ms = opened * 1000.0 if opened < 1e11 else opened
        interval = float(getattr(frame, "attrs", {}).get("timeframe_ms") or 60_000)
        # An OHLC candle cannot tell whether its extreme happened before or
        # after an intrabar fill. Count the entry candle only when entry was
        # exactly at its open; otherwise begin with the next complete candle.
        first_fully_held_bar = math.ceil(opened_ms / interval) * interval
        closed = closed.loc[
            closed["timestamp"].astype(float) >= first_fully_held_bar
        ].copy()
    return closed


def _closed_snapshot_fresh(frame: Any, closed: Any) -> bool:
    """Reject timestamped snapshots whose last completed bar is stale/future."""
    attrs = getattr(frame, "attrs", {})
    columns = getattr(frame, "columns", ())
    if "is_closed" not in columns:
        return False
    if frame["is_closed"].isna().any():
        return False
    snapshot = _value(attrs, "snapshot_ms")
    if snapshot is None:
        return False
    if closed.empty:
        return False
    timestamp = _value(closed.iloc[-1], "timestamp")
    if timestamp is None:
        return False
    interval = _value(attrs, "timeframe_ms") or 60_000.0
    if timestamp < 1e11:
        # OHLC feeds vary between seconds and milliseconds. Prefer the unit
        # whose adjacent candle spacing matches the declared timeframe.
        delta = None
        if len(closed) >= 2:
            previous_stamp = _value(closed.iloc[-2], "timestamp")
            if previous_stamp is not None:
                delta = timestamp - previous_stamp
        if delta is not None and math.isclose(delta * 1000.0, interval, abs_tol=1.0):
            timestamp *= 1000.0
        elif delta is None:
            ms_age = snapshot - (timestamp + interval)
            sec_age = snapshot - (timestamp * 1000.0 + interval)
            if abs(sec_age) < abs(ms_age):
                timestamp *= 1000.0
    close_time = timestamp + interval
    age = snapshot - close_time
    return 0.0 <= age <= max(MAX_TREND_LOCK_STALENESS_MS, interval * 2.0)


def update_trend_lock_state(position: dict[str, Any], frame: Any) -> bool:
    """Persist whether price touched its favorable outer KC rail post-entry."""
    if not isinstance(position, dict):
        return False
    side = str(position.get("side") or "").upper()
    if side not in ("LONG", "SHORT"):
        return False
    state = position.get("trend_lock_state")
    if not isinstance(state, dict) or state.get("side") != side:
        state = {"side": side, "outer_rail_touched": False}
        position["trend_lock_state"] = state
    bars = _entry_bars(position, frame)
    if bars.empty or not _closed_snapshot_fresh(frame, bars):
        return False
    for _, row in bars.iterrows():
        extreme = _value(row, "high" if side == "LONG" else "low")
        rail = _value(row, "kc_upper" if side == "LONG" else "kc_lower")
        stamp = _value(row, "timestamp")
        if extreme is None or rail is None or rail <= 0:
            continue
        touched = extreme >= rail if side == "LONG" else extreme <= rail
        if touched and not state.get("outer_rail_touched"):
            state["outer_rail_touched"] = True
            state["outer_rail_touched_at"] = stamp
            return True
    return False


def evaluate_trend_lock_exit(
    position: Mapping[str, Any], frame: Any,
) -> dict[str, Any] | None:
    """Authorize strategy exits only after a confirmed, post-entry true pivot.

    MA crosses and KC midline breaks can occur during an ordinary pullback, so
    they are diagnostic inputs rather than standalone full-close authority.
    Hard stops and account emergencies are evaluated by their independent gate.
    """
    side = str((position or {}).get("side") or "").upper()
    if side not in ("LONG", "SHORT") or not isinstance(position, dict):
        return None
    bars = _entry_bars(position, frame)
    if len(bars) < 2 or not _closed_snapshot_fresh(frame, bars):
        return None
    update_trend_lock_state(position, frame)

    # A trend reversal signal can start well before a local peak/trough is
    # confirmed. Keep holding until the three-bar pivot confirmation (which
    # itself requires sufficient closed history and an in-position pivot).
    from core.services.exits.strict_true_pivot import confirmed_true_pivot_exit

    pivot = confirmed_true_pivot_exit(dict(position), frame)
    if pivot is None:
        return None
    pivot_reason, pivot_details = pivot
    previous, current = bars.iloc[-2], bars.iloc[-1]
    required = (
        "timestamp", "open", "high", "low", "close", "ma3", "ma15",
        "kc_upper", "kc_middle", "kc_lower",
    )
    values = [{key: _value(row, key) for key in required} for row in (previous, current)]
    if any(value is None or value <= 0 for row in values for value in row.values()):
        return None
    p, c = values
    body = abs(c["close"] - c["open"])
    price_epsilon = max(abs(c["close"]) * 1e-12, 1e-15)

    reason = pivot_reason

    previous_wick = max(0.0, p["high"] - max(p["open"], p["close"]))
    previous_lower_wick = max(0.0, min(p["open"], p["close"]) - p["low"])
    current_wick_up = max(0.0, c["high"] - max(c["open"], c["close"]))
    current_wick_down = max(0.0, min(c["open"], c["close"]) - c["low"])
    upper_values = (_value(previous, "kc_upper"), _value(current, "kc_upper"))
    lower_values = (_value(previous, "kc_lower"), _value(current, "kc_lower"))
    middle_values = (p["kc_middle"], c["kc_middle"])
    kc_middle_slope = middle_values[1] - middle_values[0]
    ma_cross = (
        "DEATH_CROSS" if p["ma3"] >= p["ma15"] and c["ma3"] < c["ma15"]
        else "GOLDEN_CROSS" if p["ma3"] <= p["ma15"] and c["ma3"] > c["ma15"]
        else "NONE"
    )
    outer_state = position.get("trend_lock_state", {})
    diagnostics = {
        "symbol": position.get("symbol"),
        "side": side,
        "qty": _value(position, "qty"),
        "bar_timestamp": _value(current, "timestamp"),
        "is_closed": True,
        "previous_bar_timestamp": _value(previous, "timestamp"),
        "previous_is_closed": True,
        "open": c["open"], "high": c["high"], "low": c["low"],
        "close": c["close"], "body": body,
        "previous_open": p["open"], "previous_high": p["high"],
        "previous_low": p["low"], "previous_close": p["close"],
        "upper_wick": current_wick_up, "lower_wick": current_wick_down,
        "previous_body": abs(p["close"] - p["open"]),
        "previous_upper_wick": previous_wick,
        "previous_lower_wick": previous_lower_wick,
        "ma3_previous": p["ma3"], "ma3_current": c["ma3"],
        "ma15_previous": p["ma15"], "ma15_current": c["ma15"],
        "ma_cross": ma_cross,
        "kc_upper_previous": upper_values[0], "kc_upper_current": upper_values[1],
        "kc_middle_previous": middle_values[0], "kc_middle_current": middle_values[1],
        "kc_lower_previous": lower_values[0], "kc_lower_current": lower_values[1],
        "kc_middle_slope": kc_middle_slope,
        "kc_direction": "UP" if kc_middle_slope > 0 else "DOWN" if kc_middle_slope < 0 else "FLAT",
        "candle_color": "GREEN" if c["close"] > c["open"] else "RED" if c["close"] < c["open"] else "DOJI",
        "outer_rail_touched": bool(outer_state.get("outer_rail_touched")),
        "outer_rail_touched_at": outer_state.get("outer_rail_touched_at"),
        "confirmed_pivot": pivot_details,
        "exit_reason": reason,
        "authorization_function": "evaluate_trend_lock_exit",
        "trigger_source": "CLOSED_BAR_TREND_LOCK",
    }
    return {
        **diagnostics,
        "reason": reason,
        "kc_middle": c["kc_middle"],
        "trend_lock": True,
    }


def is_trend_lock_emergency(reason: Any) -> bool:
    """Keep account/exchange safety exits independent from strategy locking."""
    normalized = str(reason or "").upper().replace(" ", "_")
    return any(token in normalized for token in (
        "HARD_STOP", "CIRCUIT_BREAKER", "LIQUIDATION", "MARGIN_LOSS",
        "MAXIMUM_LOSS", "OPPOSITE_KC_BAND_BREACH", "EXTREME_WATERFALL",
        "WATERFALL", "CATASTROPHIC_DUMP", "CATASTROPHIC_PUMP",
        "TWO_CLOSED_ADVERSE_ABNORMAL", "ADVERSE_STOP_LOSS",
        "MEME_ADVERSE_STOP_LOSS", "EMERGENCY_DUMP", "EMERGENCY_EXTREME",
        "FAIL_SAFE_EMERGENCY", "DATA_OUTAGE", "閃崩", "瀑布",
        "帳戶硬止損", "最大虧損", "熔斷", "反側KC", "急速反向",
    ))


def _trend_cycle_key(frame: Any, side: str) -> str:
    closed = closed_entry_candles(frame)
    if len(closed) < 2 or not _closed_snapshot_fresh(frame, closed):
        return ""
    events: list[tuple[float, str, str]] = []
    for index in range(1, len(closed)):
        previous, current = closed.iloc[index - 1], closed.iloc[index]
        stamp = _value(current, "timestamp")
        if stamp is None:
            continue
        p3, p15 = _value(previous, "ma3"), _value(previous, "ma15")
        c3, c15 = _value(current, "ma3"), _value(current, "ma15")
        po, pc = _value(previous, "open"), _value(previous, "close")
        co, cc = _value(current, "open"), _value(current, "close")
        pu = _value(previous, "kc_upper")
        cu = _value(current, "kc_upper")
        pl = _value(previous, "kc_lower")
        cl = _value(current, "kc_lower")
        long_cross = all(v is not None for v in (p3, p15, c3, c15)) and p3 <= p15 and c3 > c15
        short_cross = all(v is not None for v in (p3, p15, c3, c15)) and p3 >= p15 and c3 < c15
        long_breakout = all(v is not None for v in (po, pc, co, cc, pu, cu)) and pc <= pu and cc > cu and cc > co
        short_breakout = all(v is not None for v in (po, pc, co, cc, pl, cl)) and pc >= pl and cc < cl and cc < co
        if long_cross:
            events.append((stamp, "LONG", f"MA3_MA15_LONG_CROSS:{stamp:.0f}"))
        if short_cross:
            events.append((stamp, "SHORT", f"MA3_MA15_SHORT_CROSS:{stamp:.0f}"))
        if long_breakout:
            events.append((stamp, "LONG", f"KC_LONG_BREAKOUT:{stamp:.0f}"))
        if short_breakout:
            events.append((stamp, "SHORT", f"KC_SHORT_BREAKOUT:{stamp:.0f}"))
    if not events:
        latest_ts = _float(closed.iloc[-1].get("timestamp")) if len(closed) else None
        return f"BASELINE:{side}:{latest_ts:.0f}" if latest_ts is not None else f"BASELINE:{side}"
    _stamp, event_side, event_key = max(events, key=lambda item: item[0])
    if event_side != side:
        return f"TREND_REVERSED:{event_side}:{event_key}"
    latest_ts = _float(closed.iloc[-1].get("timestamp")) if len(closed) else None
    if latest_ts is not None and latest_ts > _stamp:
        return f"{event_key}:BAR:{latest_ts:.0f}"
    return event_key


def _trend_timeframe_key(frame: Any) -> str:
    attrs = getattr(frame, "attrs", {})
    try:
        interval = int(float(attrs.get("timeframe_ms") or 60_000))
    except (TypeError, ValueError, OverflowError):
        interval = 60_000
    return f"{interval}ms"


def evaluate_trend_cycle_entry(
    state: Mapping[str, Any], frame: Any, side: str, symbol: str = "__symbol__",
) -> tuple[bool, str, str]:
    side = str(side or "").upper()
    if side not in ("LONG", "SHORT"):
        return False, "BLOCKED_TREND_CYCLE_INVALID_SIDE", ""
    closed = closed_entry_candles(frame)
    if len(closed) < 2 or not _closed_snapshot_fresh(frame, closed):
        return False, "BLOCKED_TREND_CYCLE_CLOSED_DATA_UNAVAILABLE", ""
    key = _trend_cycle_key(frame, side)
    if key.startswith("TREND_REVERSED:"):
        return False, "BLOCKED_TREND_CYCLE_REVERSED", key
    timeframe = _trend_timeframe_key(frame)
    symbols = state.get("trend_cycle_lock", {}) if isinstance(state, Mapping) else {}
    symbol_state = symbols.get(symbol, {}) if isinstance(symbols, Mapping) else {}
    timeframe_state = symbol_state.get(timeframe, {}) if isinstance(symbol_state, Mapping) else {}
    blocked_key = timeframe_state.get(side) if isinstance(timeframe_state, Mapping) else None
    if blocked_key is None and isinstance(symbol_state, Mapping):
        legacy_key = symbol_state.get(side)
        if isinstance(legacy_key, str):
            blocked_key = legacy_key
    if blocked_key == key:
        return False, "BLOCKED_SAME_TREND_CYCLE", key
    return True, "TREND_CYCLE_AVAILABLE", key


def claim_trend_cycle(
    state: dict[str, Any], side: str, cycle_key: str,
    symbol: str = "__symbol__", timeframe: str = "60000ms",
) -> None:
    if not isinstance(state, dict) or side not in ("LONG", "SHORT") or not cycle_key:
        return
    cycles = state.setdefault("trend_cycle_lock", {})
    symbol_state = cycles.setdefault(symbol, {})
    # Migrate the old symbol->side shape without discarding its consumed keys.
    if side in symbol_state and not isinstance(symbol_state.get(side), Mapping):
        legacy = {key: value for key, value in symbol_state.items()
                  if key in ("LONG", "SHORT") and isinstance(value, str)}
        symbol_state.clear()
        symbol_state["60000ms"] = legacy
    timeframe_state = symbol_state.setdefault(timeframe, {})
    timeframe_state[side] = cycle_key


def claim_filled_trend_cycle(
    account: Any, symbol: str, side: str, cycle_key: str,
    timeframe: str = "60000ms",
) -> bool:
    """Persist the trend cycle only after a position fill is confirmed."""
    if not cycle_key:
        return False
    state = getattr(account, "entry_filter_last", None)
    if not isinstance(state, dict):
        state = account.entry_filter_last = {}
    claim_trend_cycle(
        state, str(side or "").upper(), cycle_key, symbol, timeframe,
    )
    save = getattr(account, "save_state", None)
    if callable(save):
        try:
            save(strict=True)
        except TypeError:
            save()
    return True
