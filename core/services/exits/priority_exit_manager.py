"""Ordered, evidence-backed priority exits for live positions."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Mapping
from typing import Any, Callable

import numpy as np
import pandas as pd


HTF_SLOPE_THRESHOLD = 0.015
ABNORMAL_SINGLE_ATR_MULT = 1.6
ABNORMAL_MULTI_ATR_MULT = 2.3
ABNORMAL_PCT_THRESHOLD = 0.008
CIRCUIT_BREAKER_COOLDOWN_SEC = 300
TRAILING_START_ATR = 1.5
TRAILING_DIST_ATR = 1.0
FLAT_EXIT_BUFFER_ATR = 0.3

_AUTHORIZATION_KEY = "priority_exit_authorization"
_STATE_KEY = "priority_exit_state"
_COOLDOWN_KEY = "priority_exit_cooldown_until"
_AUTHORIZATION_TTL_SEC = 30.0
_CONSOLIDATION_EXIT_VETOES = frozenset({
    "VETO_MA5_KC_MIDLINE_CHOP",
    "VETO_KC_MIDLINE_FLAT",
    "VETO_FLAT_MA5_OR_KC_MIDLINE",
    "VETO_MA5_MA15_CHOP",
    "VETO_DONCHIAN_RANGE_LOW_TREND",
})
logger = logging.getLogger(__name__)


def _number(row: Any, key: str) -> float | None:
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


def _live_ltf_pair(frame: Any, now: float) -> tuple[pd.Series, pd.Series] | None:
    if not isinstance(frame, pd.DataFrame) or len(frame) < 2:
        return None
    attrs = getattr(frame, "attrs", {})
    try:
        interval = float(attrs["timeframe_ms"])
        snapshot_ms = float(attrs["snapshot_ms"])
        if snapshot_ms < 1e11:
            snapshot_ms *= 1000.0
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    now_ms = now * 1000.0
    if (
        interval != 60_000
        or not math.isfinite(snapshot_ms)
        or not 0 <= now_ms - snapshot_ms <= 180_000
    ):
        return None
    current, previous = frame.iloc[-1], frame.iloc[-2]
    live_closed = current.get("is_closed")
    previous_closed = previous.get("is_closed")
    if (
        not isinstance(live_closed, (bool, np.bool_)) or bool(live_closed)
        or not isinstance(previous_closed, (bool, np.bool_))
        or not bool(previous_closed)
    ):
        return None
    current_ms = _timestamp_ms(current.get("timestamp"))
    previous_ms = _timestamp_ms(previous.get("timestamp"))
    if (
        current_ms is None or previous_ms is None
        or current_ms - previous_ms != interval
        or current_ms != math.floor(now_ms / interval) * interval
        or current_ms > snapshot_ms
        or previous_ms + interval > snapshot_ms
    ):
        return None
    return current, previous


def _closed_ltf_last(frame: Any) -> pd.Series | None:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    from core.services.candle_data import closed_entry_candles

    closed = closed_entry_candles(frame)
    return closed.iloc[-1] if not closed.empty else None


def _confirmed_true_pivot(
    position: Mapping[str, Any], frame: Any, quote: float,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    side = str(position.get("side") or "").upper()
    expected_reason = (
        "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT"
        if side == "LONG" else "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
    )
    try:
        from core.services.exits.strict_true_pivot import profitable_true_pivot_exit

        result = profitable_true_pivot_exit(
            dict(position), frame, dict(metadata or {}), quote=quote,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False, {}
    if result is None or result[0] != expected_reason:
        return False, {}
    evidence = result[1]
    return True, {"true_pivot_type": expected_reason, **dict(evidence or {})}


def _ltf_midline_exit_direction(
    side: str,
    frame: Any,
    live_pair: tuple[pd.Series, pd.Series] | None,
) -> str | None:
    if live_pair is None or not isinstance(frame, pd.DataFrame):
        return None
    try:
        from core.services.candle_data import closed_entry_candles

        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return None
        previous, current = closed.iloc[-2], closed.iloc[-1]
        previous_mid = _number(previous, "kc_middle")
        current_mid = _number(current, "kc_middle")
        previous_ms = _timestamp_ms(previous.get("timestamp"))
        current_ms = _timestamp_ms(current.get("timestamp"))
        if (
            previous_mid is None or previous_mid <= 0
            or current_mid is None or current_mid <= 0
            or previous_ms is None or current_ms is None
            or current_ms - previous_ms != 60_000
        ):
            return None
        if current_mid == previous_mid:
            return "FLAT"
        aligned = (
            current_mid > previous_mid
            if side == "LONG" else current_mid < previous_mid
        )
        return "ALIGNED" if aligned else "REVERSED"
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def _ltf_consolidation_evidence(
    side: str, frame: Any, live_pair: tuple[pd.Series, pd.Series] | None,
) -> dict[str, Any] | None:
    if live_pair is None or not isinstance(frame, pd.DataFrame):
        return None
    from core.services.market_consolidation_filter import evaluate_market_blacklist

    blocked, reason, evidence = evaluate_market_blacklist(
        frame, i=-2, side=side, strict_consolidation=True,
    )
    if blocked and reason in _CONSOLIDATION_EXIT_VETOES:
        return {"trigger": "LTF_CONSOLIDATION", "reason": reason, **evidence}
    return None


def _abnormal_adverse_candle(
    side: str,
    candle: Mapping[str, Any] | pd.Series,
    *,
    quote: float | None = None,
    atr: float | None = None,
) -> tuple[str | None, dict[str, Any]]:
    opening = _number(candle, "open")
    close = float(quote) if quote is not None else _number(candle, "close")
    atr_value = atr if atr is not None else _number(candle, "atr")
    if (
        opening is None or opening <= 0 or close is None
        or not math.isfinite(close) or atr_value is None
        or not math.isfinite(atr_value) or atr_value <= 0
    ):
        return None, {}
    adverse_body = opening - close if side == "LONG" else close - opening
    if adverse_body <= 0:
        return None, {}
    adverse_pct = adverse_body / opening
    trigger = None
    if adverse_body >= ABNORMAL_SINGLE_ATR_MULT * atr_value:
        trigger = "SINGLE_CANDLE_ATR"
    elif adverse_pct >= ABNORMAL_PCT_THRESHOLD:
        trigger = "SINGLE_CANDLE_PERCENT"
    return trigger, {
        "side": side,
        "candle_open": opening,
        "candle_close": close,
        "adverse_body": adverse_body,
        "adverse_pct": adverse_pct,
        "atr": atr_value,
        "kc_middle": _number(candle, "kc_middle"),
        "bar_timestamp": candle.get("timestamp"),
    }


def _confirmed_kc_midline_reversal(
    side: str, frame: Any, live_pair: tuple[pd.Series, pd.Series] | None,
) -> dict[str, Any] | None:
    if live_pair is None or not isinstance(frame, pd.DataFrame) or len(frame) < 3:
        return None
    try:
        from core.services.candle_data import closed_entry_candles

        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return None
        prior, previous, current = frame.iloc[-3], frame.iloc[-2], frame.iloc[-1]
        middle_values = tuple(
            _number(row, "kc_middle") for row in (prior, previous, current)
        )
        if any(value is None for value in middle_values):
            return None
        is_long = side == "LONG"
        midline_turned = (
            middle_values[2] < middle_values[1] < middle_values[0]
            if is_long
            else middle_values[2] > middle_values[1] > middle_values[0]
        )
        if not midline_turned:
            return None

        last_closed = closed.iloc[-1]
        last_close = _number(last_closed, "close")
        outer_band = _number(
            last_closed, "kc_lower" if is_long else "kc_upper",
        )
        outer_break = (
            last_close is not None and outer_band is not None
            and (last_close < outer_band if is_long else last_close > outer_band)
        )

        previous_closed = closed.iloc[-2]
        closes = (
            _number(previous_closed, "close"),
            _number(last_closed, "close"),
        )
        closed_middles = (
            _number(previous_closed, "kc_middle"),
            _number(last_closed, "kc_middle"),
        )
        ma15_values = (
            _number(previous_closed, "ma15"),
            _number(last_closed, "ma15"),
        )
        two_closes_beyond_midline = (
            all(value is not None for value in (*closes, *closed_middles))
            and (
                closes[0] < closed_middles[0]
                and closes[1] < closed_middles[1]
                if is_long
                else closes[0] > closed_middles[0]
                and closes[1] > closed_middles[1]
            )
        )
        ma15_turned = (
            all(value is not None for value in ma15_values)
            and (
                ma15_values[1] < ma15_values[0]
                if is_long else ma15_values[1] > ma15_values[0]
            )
        )
        if not outer_break and not (two_closes_beyond_midline and ma15_turned):
            return None
        return {
            "trigger": "KC_MIDLINE_REVERSAL_AND_STRUCTURE_BREAK",
            "side": side,
            "midline_values": middle_values,
            "closed_close": last_close,
            "closed_outer_band": outer_band,
            "structure_confirmation": (
                "CLOSED_CLOSE_BEYOND_OPPOSITE_OUTER_BAND"
                if outer_break
                else "TWO_CLOSES_BEYOND_MIDLINE_WITH_MA15_TURN"
            ),
            "ma15_values": ma15_values,
        }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def _verified_waterfall_cross(side, opening, close, atr, middle):
    """Only a configured abnormal body crossing KC middle is a waterfall exit."""
    try:
        opening, close, atr, middle = map(float, (opening, close, atr, middle))
        if not all(math.isfinite(value) and value > 0 for value in (opening, close, atr, middle)):
            return False
        adverse_body = opening - close if side == "LONG" else close - opening
        crossed = (
            opening >= middle and close < middle if side == "LONG"
            else opening <= middle and close > middle
        )
        return adverse_body >= ABNORMAL_SINGLE_ATR_MULT * atr and crossed
    except (TypeError, ValueError, OverflowError):
        return False


def _verified_consecutive_waterfall(side, first, second):
    """Require a cumulative abnormal move and a KC-middle cross."""
    try:
        opening = float(first["candle_open"])
        close = float(second["candle_close"])
        atr = max(float(first["atr"]), float(second["atr"]))
        first_middle = float(first["kc_middle"])
        second_middle = float(second["kc_middle"])
        crossed = (
            opening >= first_middle and close < second_middle if side == "LONG"
            else opening <= first_middle and close > second_middle
        )
        adverse_body = opening - close if side == "LONG" else close - opening
        if (not all(math.isfinite(value) and value > 0 for value in
                    (opening, close, atr, first_middle, second_middle))
                or adverse_body < ABNORMAL_MULTI_ATR_MULT * atr
                or not crossed):
            return None
        return {
            "adverse_body": adverse_body,
            "atr": atr,
            "kc_middle_first": first_middle,
            "kc_middle_second": second_middle,
            "kc_middle_crossed": True,
            "trigger": "CONSECUTIVE_WATERFALL_2_3_ATR_KC_CROSS",
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _update_ma5_turn_state(
    position: Mapping[str, Any],
    frame: Any,
    quote: float,
    state: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None, False
    try:
        from core.services.candle_data import closed_entry_candles

        closed = closed_entry_candles(frame)
        if len(closed) < 4:
            return None, False
        closes = pd.to_numeric(closed["close"].tail(4), errors="coerce").to_numpy(
            dtype=float,
        )
        values = [*closes, float(quote)]
        if not all(math.isfinite(value) and value > 0 for value in values):
            return None, False
        ma5 = sum(values) / 5.0
        opening = _timestamp_ms(
            position.get("open_timestamp") or position.get("entry_timestamp")
        )
        live_bar_ms = _timestamp_ms(frame.iloc[-1].get("timestamp"))
        side = str(position.get("side") or "").upper()
        if opening is None or live_bar_ms is None or side not in ("LONG", "SHORT"):
            return None, False
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None, False

    key = "ma5_turn_state"
    turn_state = state.get(key)
    if not isinstance(turn_state, dict):
        turn_state = {}
        state[key] = turn_state
    identity_matches = (
        turn_state.get("side") == side
        and turn_state.get("position_open_ms") == opening
    )
    try:
        previous_ma5 = float(turn_state["last_ma5"]) if identity_matches else math.nan
        previous_bar_ms = float(turn_state["bar_ms"]) if identity_matches else math.nan
    except (KeyError, TypeError, ValueError, OverflowError):
        previous_ma5 = previous_bar_ms = math.nan

    durable_before = (
        turn_state.get("side"),
        turn_state.get("position_open_ms"),
        turn_state.get("bar_ms"),
        turn_state.get("favorable_seen"),
        turn_state.get("adverse_ticks"),
    )
    if not identity_matches or previous_bar_ms != live_bar_ms:
        turn_state.update({
            "side": side,
            "position_open_ms": opening,
            "bar_ms": live_bar_ms,
            "last_ma5": ma5,
            "favorable_seen": (
                bool(turn_state.get("favorable_seen")) if identity_matches else False
            ),
            "adverse_ticks": 0,
        })
        return None, durable_before != (
            turn_state.get("side"),
            turn_state.get("position_open_ms"),
            turn_state.get("bar_ms"),
            turn_state.get("favorable_seen"),
            turn_state.get("adverse_ticks"),
        )

    epsilon = max(abs(ma5), abs(previous_ma5), 1.0) * 1e-12
    delta = ma5 - previous_ma5
    favorable = delta > epsilon if side == "LONG" else delta < -epsilon
    adverse = delta < -epsilon if side == "LONG" else delta > epsilon
    if favorable:
        turn_state["favorable_seen"] = True
        turn_state["adverse_ticks"] = 0
    elif adverse and turn_state.get("favorable_seen") is True:
        turn_state["adverse_ticks"] = int(turn_state.get("adverse_ticks") or 0) + 1
    else:
        turn_state["adverse_ticks"] = 0
    turn_state["last_ma5"] = ma5
    durable_after = (
        turn_state.get("side"),
        turn_state.get("position_open_ms"),
        turn_state.get("bar_ms"),
        turn_state.get("favorable_seen"),
        turn_state.get("adverse_ticks"),
    )
    changed = durable_before != durable_after
    if turn_state["adverse_ticks"] < 2:
        return None, changed
    return {
        "trigger": "TWO_CONSECUTIVE_LIVE_MA5_COUNTERTREND_OBSERVATIONS",
        "side": side,
        "quote": float(quote),
        "ma5": ma5,
        "previous_ma5": previous_ma5,
        "ma5_delta": delta,
        "adverse_observations": turn_state["adverse_ticks"],
        "bar_timestamp": live_bar_ms,
    }, changed


def _valid_authorization(
    position: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
    reason: str,
    quote: float,
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    auth = (metadata or {}).get(_AUTHORIZATION_KEY)
    if not isinstance(auth, Mapping):
        return None
    current_time = time.time() if now is None else float(now)
    try:
        issued_at = float(auth["issued_at"])
        expected_quote = float(auth["quote"])
        actual_quote = float(quote)
        expected_open = float(auth["position_open_timestamp"])
        actual_open = float(
            position.get("open_timestamp")
            or position.get("entry_timestamp")
            or (metadata or {}).get("open_timestamp")
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if actual_open >= 1e11:
        actual_open /= 1000.0
    if expected_open >= 1e11:
        expected_open /= 1000.0
    if (
        str(auth.get("reason") or "") != str(reason)
        or str(auth.get("side") or "").upper()
        != str(position.get("side") or "").upper()
        or (
            auth.get("position_uid") is not None
            and str(auth.get("position_uid")) != str(position.get("position_uid") or "")
        )
        or not all(math.isfinite(value) for value in (
            issued_at, expected_quote, actual_quote, expected_open, actual_open,
        ))
        or not 0 <= current_time - issued_at <= _AUTHORIZATION_TTL_SEC
        or not math.isclose(
            expected_quote, actual_quote, rel_tol=1e-12, abs_tol=1e-12,
        )
        or not math.isclose(expected_open, actual_open, rel_tol=0, abs_tol=0.001)
        or not isinstance(auth.get("evidence"), Mapping)
    ):
        return None
    return dict(auth["evidence"])


def entry_circuit_breaker_cooldown_problem(
    account: Any, symbol: str, *, now: float | None = None,
) -> str | None:
    state = getattr(account, "entry_filter_last", None)
    if not isinstance(state, dict) or _COOLDOWN_KEY not in state:
        return None
    cooldowns = state.get(_COOLDOWN_KEY)
    if not isinstance(cooldowns, dict):
        return "BLOCKED_CIRCUIT_BREAKER_COOLDOWN_STATE_INVALID"
    if symbol not in cooldowns:
        return None
    until = cooldowns.get(symbol) if isinstance(cooldowns, dict) else None
    try:
        until_value = float(until)
        current_time = time.time() if now is None else float(now)
    except (TypeError, ValueError, OverflowError):
        return "BLOCKED_CIRCUIT_BREAKER_COOLDOWN_STATE_INVALID"
    if not math.isfinite(until_value) or not math.isfinite(current_time):
        return "BLOCKED_CIRCUIT_BREAKER_COOLDOWN_STATE_INVALID"
    if current_time < until_value:
        return "BLOCKED_CIRCUIT_BREAKER_COOLDOWN"
    if isinstance(cooldowns, dict):
        cooldowns.pop(symbol, None)
    return None


def evaluate_priority_exit(
    position: Mapping[str, Any],
    frame_1m: Any,
    frame_15m: Any,
    quote: float,
    metadata: dict[str, Any],
    *,
    profit_protection: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Evaluate the P0-P3 emergency and confirmed KC exit policy."""
    current_time = time.time() if now is None else float(now)
    try:
        price = float(quote)
    except (TypeError, ValueError, OverflowError):
        return {"priority": None, "reason": None, "evidence": {}, "state_changed": False}
    if not math.isfinite(current_time) or not math.isfinite(price) or price <= 0:
        return {"priority": None, "reason": None, "evidence": {}, "state_changed": False}

    side = str(position.get("side") or "").upper()
    if side not in ("LONG", "SHORT"):
        return {"priority": None, "reason": None, "evidence": {}, "state_changed": False}

    state = metadata.get(_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
        metadata[_STATE_KEY] = state
    state_changed = False

    live_pair = _live_ltf_pair(frame_1m, current_time)
    live, previous = live_pair if live_pair is not None else (None, None)
    live_atr = _number(live, "atr") if live is not None else None
    if live_atr is None and previous is not None:
        live_atr = _number(previous, "atr")

    htf: dict[str, Any] = {"valid": False}
    if frame_15m is not None:
        from core.services.mtf_kc_state_machine import evaluate_htf_kc_state
        htf = evaluate_htf_kc_state(frame_15m)
    htf_state = str(htf.get("state") or "").upper() if htf.get("valid") else ""
    previous_htf_state = str(state.get("htf_state") or "").upper()
    trend_hold_reason = None
    abnormal_hold_reason = None

    if live is not None and live_atr is not None and live_atr > 0:
        interval_ms = 60_000.0
        live_bar_ms = _timestamp_ms(live.get("timestamp"))
        previous_bar_ms = _timestamp_ms(previous.get("timestamp"))
        position_open_ms = _timestamp_ms(
            position.get("open_timestamp") or position.get("entry_timestamp")
        )
        pending = state.get("abnormal_exit_pending")
        if not isinstance(pending, dict):
            pending = None
        if pending is not None and (
            pending.get("side") != side
            or _timestamp_ms(pending.get("bar_timestamp")) is None
            or pending.get("position_open_ms") != position_open_ms
        ):
            state.pop("abnormal_exit_pending", None)
            pending = None
            state_changed = True
        pending_bar_ms = (
            _timestamp_ms(pending.get("bar_timestamp"))
            if pending is not None else None
        )
        if pending is not None and pending.get("confirmed") is True:
            return {
                "priority": 0,
                "reason": "PRIORITY_EXIT_P0_CIRCUIT_BREAKER",
                "cooldown": True,
                "state_changed": state_changed,
                "evidence": {
                    **dict(pending.get("confirmed_evidence") or {}),
                    "trigger": "CONSECUTIVE_ABNORMAL_CANDLES",
                    "side": side,
                    "quote": price,
                    "close_retry": True,
                },
            }
        current_trigger, current_evidence = _abnormal_adverse_candle(
            side, live, quote=price, atr=live_atr,
        )
        if current_trigger == "SINGLE_CANDLE_PERCENT":
            return {
                "priority": 0,
                "reason": "PRIORITY_EXIT_P0_CIRCUIT_BREAKER",
                "cooldown": True,
                "state_changed": state_changed,
                "evidence": {
                    **current_evidence,
                    "trigger": current_trigger,
                    "side": side,
                    "quote": price,
                    "threshold_pct": ABNORMAL_PCT_THRESHOLD,
                },
            }
        current_middle = _number(live, "kc_middle")
        if (current_trigger == "SINGLE_CANDLE_ATR"
                and current_middle is not None
                and _verified_waterfall_cross(
                    side, current_evidence["candle_open"], price,
                    current_evidence["atr"], current_middle,
                )):
            return {
                "priority": 0,
                "reason": "PRIORITY_EXIT_P0_CIRCUIT_BREAKER",
                "cooldown": True,
                "state_changed": state_changed,
                "evidence": {
                    "trigger": "SINGLE_WATERFALL_1_6_ATR_KC_CROSS",
                    "side": side,
                    "quote": price,
                    "waterfall": current_evidence,
                    "kc_middle": current_middle,
                },
            }
        second_trigger = None
        second_evidence: dict[str, Any] = {}
        if pending is not None and pending_bar_ms is not None:
            if live_bar_ms == pending_bar_ms + interval_ms and current_trigger:
                second_trigger = current_trigger
                second_evidence = current_evidence
            elif (
                previous_bar_ms == pending_bar_ms + interval_ms
                and previous is not None
            ):
                second_trigger, second_evidence = _abnormal_adverse_candle(
                    side, previous,
                )
                if second_trigger is None:
                    state.pop("abnormal_exit_pending", None)
                    pending = None
                    pending_bar_ms = None
                    state_changed = True
            elif (
                live_bar_ms is not None
                and live_bar_ms > pending_bar_ms + interval_ms
            ):
                state.pop("abnormal_exit_pending", None)
                pending = None
                pending_bar_ms = None
                state_changed = True

        if second_trigger:
            first_evidence = pending.get("evidence", {})
            second_candle_evidence = {
                **second_evidence,
                "trigger": second_trigger,
                "quote": price if live_bar_ms == pending_bar_ms + interval_ms
                else second_evidence.get("candle_close"),
            }
            waterfall = _verified_consecutive_waterfall(
                side, first_evidence, second_candle_evidence,
            )
            if waterfall is None:
                state.pop("abnormal_exit_pending", None)
                state_changed = True
                abnormal_hold_reason = "CONSECUTIVE_ABNORMAL_BARS_NOT_WATERFALL"
            else:
                pending["confirmed"] = True
                pending["confirmed_evidence"] = {
                    "first_abnormal_candle": first_evidence,
                    "second_abnormal_candle": second_candle_evidence,
                    **waterfall,
                }
                state_changed = True
                return {
                    "priority": 0,
                    "reason": "PRIORITY_EXIT_P0_CIRCUIT_BREAKER",
                    "cooldown": True,
                    "state_changed": state_changed,
                    "evidence": {
                        **pending["confirmed_evidence"],
                        "trigger": "CONSECUTIVE_WATERFALL_2_3_ATR_KC_CROSS",
                        "side": side,
                        "quote": price,
                    },
                }

        if pending is not None:
            abnormal_hold_reason = "WAITING_FOR_SECOND_CONSECUTIVE_ABNORMAL_CANDLE"

        if current_trigger:
            if pending is None:
                state["abnormal_exit_pending"] = {
                    "side": side,
                    "bar_timestamp": live_bar_ms,
                    "position_open_ms": position_open_ms,
                    "trigger": current_trigger,
                    "evidence": {**current_evidence, "trigger": current_trigger},
                }
                state_changed = True
                abnormal_hold_reason = (
                    "WAITING_FOR_SECOND_CONSECUTIVE_ABNORMAL_CANDLE"
                )
        elif pending is None:
            abnormal_hold_reason = None

    if htf.get("valid"):
        if htf_state != previous_htf_state:
            state["htf_state"] = htf_state
            state_changed = True

    # ── 1. 真峰谷平倉最高優先級：若遇真峰谷，立即授權平倉落袋！ ──
    pivot_confirmed, pivot_evidence = _confirmed_true_pivot(
        position, frame_1m, price, metadata,
    )
    if pivot_confirmed:
        return {
            "priority": 1,
            "reason": "PRIORITY_EXIT_P1_TRUE_PEAK_VALLEY",
            "state_changed": state_changed,
            "evidence": {
                **pivot_evidence,
                "side": side,
                "quote": price,
            },
        }

    # ── 2. 中軌方向大週期方向不變就不要平倉：死抱主升/主跌段！ ──
    midline_exit_direction = _ltf_midline_exit_direction(
        side, frame_1m, live_pair,
    )
    htf_aligned = (
        (side == "LONG" and htf_state == "UPTREND")
        or (side == "SHORT" and htf_state == "DOWNTREND")
    )
    if midline_exit_direction != "REVERSED" and (htf_aligned or midline_exit_direction == "ALIGNED"):
        if midline_exit_direction == "ALIGNED":
            hold_reason = (
                "LONG_HOLD_KC_MIDLINE_STILL_UP_WAIT_TRUE_PEAK"
                if side == "LONG"
                else "SHORT_HOLD_KC_MIDLINE_STILL_DOWN_WAIT_TRUE_VALLEY"
            )
        elif midline_exit_direction == "FLAT":
            hold_reason = (
                "LONG_HOLD_KC_MIDLINE_NOT_TURNED_DOWN"
                if side == "LONG"
                else "SHORT_HOLD_KC_MIDLINE_NOT_TURNED_UP"
            )
        else:
            hold_reason = (
                "LONG_HOLD_KC_MIDLINE_DATA_UNAVAILABLE"
                if side == "LONG"
                else "SHORT_HOLD_KC_MIDLINE_DATA_UNAVAILABLE"
            )
        return {
            "priority": None,
            "reason": None,
            "state_changed": state_changed,
            "htf_valid": bool(htf.get("valid")),
            "htf_reason": htf.get("reason"),
            "hold_reason": hold_reason,
            "evidence": {
                "htf_state": htf_state or None,
                "ltf_midline_exit_direction": midline_exit_direction,
            },
        }

    if isinstance(profit_protection, Mapping):
        protection_status = str(profit_protection.get("status") or "")
        if protection_status == "BLOCKED":
            logger.error(
                "POSITION_PROFIT_PROTECTION_BLOCKED symbol=%s side=%s reason=%s",
                position.get("symbol"),
                side,
                profit_protection.get("reason") or "BLOCKED_POSITION_PROTECTION_STATE",
            )
        protection_reason = profit_protection.get("reason")
        if protection_status == "READY" and protection_reason:
            evidence = profit_protection.get("evidence")
            if not isinstance(evidence, Mapping):
                return {
                    "priority": None,
                    "reason": None,
                    "state_changed": state_changed,
                    "hold_reason": "BLOCKED_POSITION_PROTECTION_EVIDENCE_INVALID",
                    "evidence": {},
                }
            return {
                "priority": 1,
                "reason": str(protection_reason),
                "state_changed": state_changed,
                "evidence": dict(evidence),
            }

    consolidation_evidence = _ltf_consolidation_evidence(
        side, frame_1m, live_pair,
    )
    if consolidation_evidence is not None:
        return {
            "priority": None,
            "reason": None,
            "state_changed": state_changed,
            "htf_valid": bool(htf.get("valid")),
            "htf_reason": htf.get("reason"),
            "hold_reason": f"{side}_HOLD_LTF_CONSOLIDATION",
            "evidence": consolidation_evidence,
        }

    if htf.get("valid"):
        htf_slope = _number(htf, "normalized_slope")
        htf_reversed = (
            htf_slope is not None
            and (
                side == "LONG" and htf_slope < -HTF_SLOPE_THRESHOLD
                or side == "SHORT" and htf_slope > HTF_SLOPE_THRESHOLD
            )
        )
        if htf_reversed:
            return {
                "priority": 1,
                "reason": "PRIORITY_EXIT_P1_HTF_KC_REVERSAL",
                "state_changed": state_changed,
                "evidence": {
                    "trigger": "HTF_KC_MIDLINE_REVERSED",
                    "side": side,
                    "normalized_slope": htf_slope,
                    "slope_threshold": HTF_SLOPE_THRESHOLD,
                    "htf_state": htf_state,
                    "bar_timestamp": htf.get("bar_timestamp"),
                },
            }

    kc_reversal_evidence = _confirmed_kc_midline_reversal(
        side, frame_1m, live_pair,
    )
    if kc_reversal_evidence is not None:
        return {
            "priority": 2,
            "reason": "PRIORITY_EXIT_P2_KC_MIDLINE_REVERSAL",
            "state_changed": state_changed,
            "evidence": {**kc_reversal_evidence, "quote": price},
        }

    if htf.get("valid"):
        band = _number(htf, "kc_lower" if side == "LONG" else "kc_upper")
        band_hit = (
            band is not None and (price < band if side == "LONG" else price > band)
        )
        if band_hit:
            return {
                "priority": 3,
                "reason": "PRIORITY_EXIT_P3_OPPOSITE_HTF_KC_BAND",
                "state_changed": state_changed,
                "evidence": {
                    "trigger": "OPPOSITE_HTF_KC_BAND",
                    "side": side,
                    "quote": price,
                    "band": band,
                    "bar_timestamp": htf.get("bar_timestamp"),
                },
            }

    trend_hold_reason = (
        "LONG_HOLD_TRUE_PEAK_OR_STRUCTURE_NOT_CONFIRMED"
        if side == "LONG"
        else "SHORT_HOLD_TRUE_VALLEY_OR_STRUCTURE_NOT_CONFIRMED"
    )
    return {
        "priority": None,
        "reason": None,
        "state_changed": state_changed,
        "htf_valid": bool(htf.get("valid")),
        "htf_reason": htf.get("reason"),
        "hold_reason": trend_hold_reason or abnormal_hold_reason,
        "evidence": {"htf_state": htf_state or None},
    }


async def evaluate_and_execute_exit(
    account: Any,
    symbol: str,
    position: Mapping[str, Any] | None,
    frame_1m: Any,
    frame_15m: Any,
    quote: float,
    close_position: Callable[..., Any],
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Evaluate the priority tree and route authorized exits through one closer."""
    if not isinstance(position, Mapping):
        return None
    current_time = time.time() if now is None else float(now)
    metadata_map = getattr(account, "position_meta", None)
    if not isinstance(metadata_map, dict):
        metadata_map = {}
        account.position_meta = metadata_map
    metadata = metadata_map.setdefault(symbol, {})
    if not isinstance(metadata, dict):
        metadata = {}
        metadata_map[symbol] = metadata

    from core.services.exits.position_profit_protection import (
        evaluate_and_persist_position_protection,
    )

    profit_protection = evaluate_and_persist_position_protection(
        symbol, position, frame_1m, quote, now=current_time,
    )
    if (
        profit_protection.get("status") == "READY"
        and profit_protection.get("position_uid_created") is True
    ):
        position_uid = str(profit_protection.get("position_uid") or "")
        if not isinstance(position, dict) or not position_uid:
            profit_protection = {
                "status": "BLOCKED",
                "reason": "POSITION_PROTECTION_UID_PERSIST_UNAVAILABLE",
            }
        else:
            position["position_uid"] = position_uid
            if not _persist_account_state(account, symbol, "POSITION_EXIT_UID"):
                if position.get("position_uid") == position_uid:
                    position.pop("position_uid", None)
                profit_protection = {
                    "status": "BLOCKED",
                    "reason": "POSITION_PROTECTION_UID_PERSIST_FAILED",
                }

    decision = evaluate_priority_exit(
        position,
        frame_1m,
        frame_15m,
        quote,
        metadata,
        profit_protection=profit_protection,
        now=current_time,
    )
    if decision.get("priority") == 1 and metadata.pop(
        "mtf_pending_closed_bar_exit", None,
    ) is not None:
        decision["state_changed"] = True
    if decision.get("state_changed"):
        _persist_account_state(account, symbol, "PRIORITY_EXIT_TRACKER")
    reason = decision.get("reason")
    if not reason:
        hold_reason = str(decision.get("hold_reason") or "")
        if hold_reason.endswith("_HOLD_LTF_CONSOLIDATION"):
            logger.info(
                "[震盪續抱 | 不執行策略平倉] symbol=%s side=%s evidence=%s",
                symbol, position.get("side"), dict(decision.get("evidence") or {}),
            )
        elif hold_reason.startswith("LONG_HOLD_"):
            logger.info(
                "[多單死抱中 | 1m KC 中軌尚未確認轉下或真峰頂未確認] "
                "symbol=%s quote=%s",
                symbol, quote,
            )
        elif hold_reason.startswith("SHORT_HOLD_"):
            logger.info(
                "[空單死抱中 | 1m KC 中軌尚未確認轉上或真谷底未確認] "
                "symbol=%s quote=%s",
                symbol, quote,
            )
        return {
            "status": "HOLD",
            "symbol": symbol,
            "reason": (
                str(decision.get("hold_reason")
                    or decision.get("htf_reason")
                    or "BLOCKED_HTF_KC_LIVE_DATA_INVALID")
                if (
                    decision.get("hold_reason")
                    or (frame_15m is not None and not decision.get("htf_valid"))
                )
                else "PRIORITY_EXIT_NO_TRIGGER"
            ),
            "priority": None,
            "success": False,
        }

    evidence = decision.get("evidence")
    if not isinstance(evidence, Mapping):
        logger.error("PRIORITY_EXIT_EVIDENCE_INVALID symbol=%s reason=%s", symbol, reason)
        return {
            "status": "HOLD", "symbol": symbol,
            "reason": "BLOCKED_PRIORITY_EXIT_EVIDENCE_INVALID",
            "priority": decision.get("priority"), "success": False,
        }
    if reason == "PRIORITY_EXIT_P1_TRUE_PEAK_VALLEY":
        pivot_reason = str(evidence.get("true_pivot_type") or "")
        pivot_details = {
            key: value for key, value in evidence.items()
            if key not in ("true_pivot_type", "side", "quote")
        }
        try:
            from core.services.exits.strict_true_pivot import (
                PROFIT_PIVOT_REASONS,
                persist_profit_pivot_exit,
            )

            if (
                not isinstance(position, dict)
                or pivot_reason not in PROFIT_PIVOT_REASONS
            ):
                raise ValueError("confirmed pivot evidence is incomplete")
            persist_profit_pivot_exit(
                account, symbol, position, pivot_reason, pivot_details,
                strict_save=True,
            )
        except Exception:
            logger.exception(
                "PRIORITY_EXIT_PIVOT_PERSIST_FAILED symbol=%s reason=%s",
                symbol, reason,
            )
            return {
                "status": "HOLD",
                "symbol": symbol,
                "reason": "BLOCKED_PRIORITY_EXIT_PIVOT_PERSIST_FAILED",
                "priority": decision.get("priority"),
                "success": False,
            }
    if decision.get("cooldown"):
        entry_state = getattr(account, "entry_filter_last", None)
        if not isinstance(entry_state, dict):
            entry_state = {}
            account.entry_filter_last = entry_state
        cooldowns = entry_state.setdefault(_COOLDOWN_KEY, {})
        if not isinstance(cooldowns, dict):
            logger.critical("PRIORITY_EXIT_COOLDOWN_STATE_INVALID symbol=%s", symbol)
            return {
                "status": "HOLD", "symbol": symbol,
                "reason": "BLOCKED_CIRCUIT_BREAKER_COOLDOWN_STATE_INVALID",
                "priority": 0, "success": False,
            }
        try:
            previous_cooldown = float(cooldowns.get(symbol) or 0.0)
        except (TypeError, ValueError, OverflowError):
            logger.critical("PRIORITY_EXIT_COOLDOWN_STATE_INVALID symbol=%s", symbol)
            return {
                "status": "HOLD", "symbol": symbol,
                "reason": "BLOCKED_CIRCUIT_BREAKER_COOLDOWN_STATE_INVALID",
                "priority": 0, "success": False,
            }
        if not math.isfinite(previous_cooldown):
            logger.critical("PRIORITY_EXIT_COOLDOWN_STATE_INVALID symbol=%s", symbol)
            return {
                "status": "HOLD", "symbol": symbol,
                "reason": "BLOCKED_CIRCUIT_BREAKER_COOLDOWN_STATE_INVALID",
                "priority": 0, "success": False,
            }
        cooldowns[symbol] = max(
            previous_cooldown,
            current_time + CIRCUIT_BREAKER_COOLDOWN_SEC,
        )
        _persist_account_state(account, symbol, "PRIORITY_EXIT_COOLDOWN")

    try:
        open_timestamp = float(
            position.get("open_timestamp")
            or position.get("entry_timestamp")
            or metadata.get("open_timestamp")
        )
    except (TypeError, ValueError, OverflowError):
        logger.error("PRIORITY_EXIT_POSITION_ID_INVALID symbol=%s", symbol)
        return {
            "status": "HOLD", "symbol": symbol,
            "reason": "BLOCKED_PRIORITY_EXIT_POSITION_ID_INVALID",
            "priority": decision.get("priority"), "success": False,
        }
    metadata[_AUTHORIZATION_KEY] = {
        "reason": str(reason),
        "side": str(position.get("side") or "").upper(),
        "position_uid": position.get("position_uid"),
        "quote": float(quote),
        "position_open_timestamp": open_timestamp,
        "issued_at": current_time,
        "evidence": dict(evidence),
    }
    _persist_account_state(account, symbol, "PRIORITY_EXIT_AUTHORIZATION")
    priority = decision.get("priority")
    if priority == 0:
        logger.critical(
            "[🚨緊急熔斷平倉] symbol=%s side=%s reason=%s evidence=%s",
            symbol, position.get("side"), reason, dict(evidence),
        )
    else:
        priority_label = {
            1: "獲利保護或 15m KC 趨勢反轉",
            2: "1m KC 中軌轉向與結構破壞",
            3: "ATR 硬止損或 15m 反側外軌破壞",
        }.get(priority, "未分類")
        logger.warning(
            "[平倉執行 | Priority %s %s] symbol=%s side=%s reason=%s evidence=%s",
            priority, priority_label, symbol, position.get("side"),
            reason, dict(evidence),
        )
    close_result = close_position(
        account,
        symbol,
        float(quote),
        str(reason),
        meta={
            "raw_df": frame_1m,
            "frame_15m": frame_15m,
            "is_bar_closed": False,
            "priority_exit_evidence": dict(evidence),
        },
    )
    if hasattr(close_result, "__await__"):
        close_result = await close_result
    success = bool(close_result)
    return {
        "status": "EXIT_EXECUTED" if success else "HOLD",
        "symbol": symbol,
        "reason": str(reason) if success else f"EXIT_BLOCKED_HOLD:{reason}",
        "priority": decision.get("priority"),
        "evidence": dict(evidence),
        "success": success,
    }


def _persist_account_state(account: Any, symbol: str, operation: str) -> bool:
    save_state = getattr(account, "save_state", None)
    if not callable(save_state):
        logger.error("%s_PERSIST_UNAVAILABLE symbol=%s", operation, symbol)
        return False
    try:
        try:
            save_state(strict=True)
        except TypeError:
            save_state()
    except Exception:
        logger.exception("%s_PERSIST_FAILED symbol=%s", operation, symbol)
        return False
    return True


__all__ = [
    "HTF_SLOPE_THRESHOLD",
    "ABNORMAL_SINGLE_ATR_MULT",
    "ABNORMAL_MULTI_ATR_MULT",
    "ABNORMAL_PCT_THRESHOLD",
    "CIRCUIT_BREAKER_COOLDOWN_SEC",
    "TRAILING_START_ATR",
    "TRAILING_DIST_ATR",
    "FLAT_EXIT_BUFFER_ATR",
    "entry_circuit_breaker_cooldown_problem",
    "evaluate_priority_exit",
    "evaluate_and_execute_exit",
    "_valid_authorization",
]
