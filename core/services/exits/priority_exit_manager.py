"""Ordered, evidence-backed priority exits for live positions."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Mapping
from typing import Any, Callable

import numpy as np
import pandas as pd


ABNORMAL_SINGLE_ATR_MULT = 1.5
ABNORMAL_MULTI_ATR_MULT = 2.2
ABNORMAL_PCT_THRESHOLD = 0.008
CIRCUIT_BREAKER_COOLDOWN_SEC = 300
HARD_SL_ATR_MULT = 2.0
TRAILING_START_ATR = 1.5
TRAILING_DIST_ATR = 1.0
FLAT_EXIT_BUFFER_ATR = 0.3

_AUTHORIZATION_KEY = "priority_exit_authorization"
_STATE_KEY = "priority_exit_state"
_COOLDOWN_KEY = "priority_exit_cooldown_until"
_AUTHORIZATION_TTL_SEC = 30.0
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
    now: float | None = None,
) -> dict[str, Any]:
    """Evaluate P0-P4 in strict priority order and update persistent trackers."""
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
    entry = _number(position, "entry_price")
    entry_atr = (
        _number(position, "entry_atr")
        or _number(metadata, "entry_atr")
        or _number(position, "atr")
        or _number(metadata, "atr")
    )
    live_atr = _number(live, "atr") if live is not None else None
    if live_atr is None and previous is not None:
        live_atr = _number(previous, "atr")

    htf: dict[str, Any] = {"valid": False}
    if frame_15m is not None:
        from core.services.mtf_kc_state_machine import evaluate_htf_kc_state

        htf = evaluate_htf_kc_state(frame_15m)
    htf_state = str(htf.get("state") or "").upper() if htf.get("valid") else ""
    previous_htf_state = str(state.get("htf_state") or "").upper()

    if live is not None and live_atr is not None and live_atr > 0:
        opening = _number(live, "open")
        previous_open = _number(previous, "open")
        if opening is not None and opening > 0:
            adverse_body = opening - price if side == "LONG" else price - opening
            adverse_pct = adverse_body / opening
            cumulative = (
                previous_open - price if side == "LONG"
                else price - previous_open
            ) if previous_open is not None else None
            single_hit = adverse_body >= ABNORMAL_SINGLE_ATR_MULT * live_atr
            percent_hit = adverse_pct >= ABNORMAL_PCT_THRESHOLD
            multi_hit = (
                cumulative is not None
                and cumulative >= ABNORMAL_MULTI_ATR_MULT * live_atr
            )
            if single_hit or percent_hit or multi_hit:
                reason = "PRIORITY_EXIT_P0_CIRCUIT_BREAKER"
                return {
                    "priority": 0,
                    "reason": reason,
                    "cooldown": True,
                    "state_changed": state_changed,
                    "evidence": {
                        "trigger": (
                            "SINGLE_CANDLE_ATR" if single_hit
                            else "SINGLE_CANDLE_PERCENT" if percent_hit
                            else "TWO_CANDLE_DISPLACEMENT"
                        ),
                        "side": side,
                        "quote": price,
                        "candle_open": opening,
                        "adverse_body": adverse_body,
                        "adverse_pct": adverse_pct,
                        "cumulative_adverse": cumulative,
                        "atr": live_atr,
                        "single_threshold": ABNORMAL_SINGLE_ATR_MULT * live_atr,
                        "multi_threshold": ABNORMAL_MULTI_ATR_MULT * live_atr,
                        "bar_timestamp": live.get("timestamp"),
                    },
                }

    if htf.get("valid"):
        if htf_state != previous_htf_state:
            state["htf_state"] = htf_state
            state_changed = True
        if side == "LONG" and htf_state == "DOWNTREND":
            return {
                "priority": 1,
                "reason": "PRIORITY_EXIT_P1_MTF_KC_REVERSAL",
                "state_changed": state_changed,
                "evidence": {**htf, "side": side, "quote": price},
            }
        if side == "SHORT" and htf_state == "UPTREND":
            return {
                "priority": 1,
                "reason": "PRIORITY_EXIT_P1_MTF_KC_REVERSAL",
                "state_changed": state_changed,
                "evidence": {**htf, "side": side, "quote": price},
            }

    if entry is not None and entry > 0 and entry_atr is not None and entry_atr > 0:
        stop = (
            entry - HARD_SL_ATR_MULT * entry_atr
            if side == "LONG"
            else entry + HARD_SL_ATR_MULT * entry_atr
        )
        hard_stop_hit = price <= stop if side == "LONG" else price >= stop
        if hard_stop_hit:
            return {
                "priority": 2,
                "reason": "PRIORITY_EXIT_P2_ENTRY_HARD_STOP",
                "state_changed": state_changed,
                "evidence": {
                    "trigger": "ENTRY_ATR_HARD_STOP",
                    "side": side,
                    "quote": price,
                    "entry_price": entry,
                    "entry_atr": entry_atr,
                    "stop_price": stop,
                },
            }
    if htf.get("valid"):
        band = _number(htf, "kc_lower" if side == "LONG" else "kc_upper")
        band_hit = (
            band is not None and (price < band if side == "LONG" else price > band)
        )
        if band_hit:
            return {
                "priority": 2,
                "reason": "PRIORITY_EXIT_P2_OPPOSITE_HTF_KC_BAND",
                "state_changed": state_changed,
                "evidence": {
                    "trigger": "OPPOSITE_HTF_KC_BAND",
                    "side": side,
                    "quote": price,
                    "band": band,
                    "bar_timestamp": htf.get("bar_timestamp"),
                },
            }

    if live_atr is not None and live_atr > 0:
        favorable_key = "favorable_extreme"
        old_extreme = _number(state, favorable_key)
        if old_extreme is None:
            old_extreme = entry if entry is not None and entry > 0 else price
        extreme = max(old_extreme, price) if side == "LONG" else min(old_extreme, price)
        if not math.isclose(extreme, old_extreme, rel_tol=1e-12, abs_tol=1e-12):
            state[favorable_key] = extreme
            state_changed = True
        favorable_move = (
            extreme - entry if side == "LONG" else entry - extreme
        ) if entry is not None else 0.0
        retracement = extreme - price if side == "LONG" else price - extreme
        if (
            entry is not None and entry > 0
            and favorable_move >= TRAILING_START_ATR * live_atr
            and retracement >= TRAILING_DIST_ATR * live_atr
        ):
            return {
                "priority": 3,
                "reason": "PRIORITY_EXIT_P3_TRAILING_LOCK",
                "state_changed": state_changed,
                "evidence": {
                    "trigger": "ATR_TRAILING_RETRACEMENT",
                    "side": side,
                    "quote": price,
                    "favorable_extreme": extreme,
                    "favorable_move": favorable_move,
                    "retracement": retracement,
                    "atr": live_atr,
                    "start_threshold": TRAILING_START_ATR * live_atr,
                    "distance_threshold": TRAILING_DIST_ATR * live_atr,
                },
            }

    if htf.get("valid"):
        flat_key = "flat_exit_armed_side"
        if htf_state == "FLAT":
            required_previous = "UPTREND" if side == "LONG" else "DOWNTREND"
            if previous_htf_state == required_previous:
                if state.get(flat_key) != side:
                    state[flat_key] = side
                    state_changed = True
        else:
            if state.pop(flat_key, None) is not None:
                state_changed = True
        if htf_state == "FLAT" and state.get(flat_key) == side:
            closed = _closed_ltf_last(frame_1m)
            close = _number(closed, "close")
            middle = _number(closed, "kc_middle")
            atr = _number(closed, "atr")
            if (
                close is not None and middle is not None
                and atr is not None and atr > 0
            ):
                threshold = middle - FLAT_EXIT_BUFFER_ATR * atr if side == "LONG" else middle + FLAT_EXIT_BUFFER_ATR * atr
                flat_exit_hit = close < threshold if side == "LONG" else close > threshold
                if flat_exit_hit:
                    return {
                        "priority": 4,
                        "reason": "PRIORITY_EXIT_P4_FLAT_MIDLINE_BUFFER",
                        "state_changed": state_changed,
                        "evidence": {
                            "trigger": "HTF_FLAT_CLOSED_LTF_BUFFER_BREAK",
                            "side": side,
                            "quote": price,
                            "closed_ltf_close": close,
                            "closed_ltf_kc_middle": middle,
                            "closed_ltf_atr": atr,
                            "buffer_atr": FLAT_EXIT_BUFFER_ATR,
                            "threshold": threshold,
                            "htf_state": htf_state,
                        },
                    }

    return {
        "priority": None,
        "reason": None,
        "state_changed": state_changed,
        "htf_valid": bool(htf.get("valid")),
        "htf_reason": htf.get("reason"),
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

    decision = evaluate_priority_exit(
        position, frame_1m, frame_15m, quote, metadata, now=current_time,
    )
    if decision.get("priority") == 1 and metadata.pop(
        "mtf_pending_closed_bar_exit", None,
    ) is not None:
        decision["state_changed"] = True
    if decision.get("state_changed"):
        _persist_account_state(account, symbol, "PRIORITY_EXIT_TRACKER")
    reason = decision.get("reason")
    if not reason:
        return {
            "status": "HOLD",
            "symbol": symbol,
            "reason": (
                str(decision.get("htf_reason") or "BLOCKED_HTF_KC_LIVE_DATA_INVALID")
                if frame_15m is not None and not decision.get("htf_valid")
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
            1: "大週期方向反轉強制清倉",
            2: "硬止損或反向軌道破壞",
            3: "棘輪移動鎖利觸發",
            4: "大週期走平緩衝出場",
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
    "ABNORMAL_SINGLE_ATR_MULT",
    "ABNORMAL_MULTI_ATR_MULT",
    "ABNORMAL_PCT_THRESHOLD",
    "CIRCUIT_BREAKER_COOLDOWN_SEC",
    "HARD_SL_ATR_MULT",
    "TRAILING_START_ATR",
    "TRAILING_DIST_ATR",
    "FLAT_EXIT_BUFFER_ATR",
    "entry_circuit_breaker_cooldown_problem",
    "evaluate_priority_exit",
    "evaluate_and_execute_exit",
    "_valid_authorization",
]
