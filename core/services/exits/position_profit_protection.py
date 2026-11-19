"""Position-bound net-ROE lock and closed-candle reversal protection."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROFIT_LOCK_START_ROI = 5.0
PROFIT_LOCK_STEP_ROI = 5.0
PROFIT_LOCK_DRAWDOWN_ROI = 3.0
OPPOSITE_CANDLE_CONFIRM_COUNT = 2
OPPOSITE_CANDLE_EXIT_ENABLED = True
OPPOSITE_CANDLE_STRONG_BODY_ATR = 0.5
_CANDLE_INTERVAL_MS = 60_000.0
_MAX_SNAPSHOT_AGE_MS = 180_000.0
_TABLE_NAME = "position_exit_protection"
logger = logging.getLogger(__name__)


def _default_db_path() -> Path:
    configured = os.environ.get("POSITION_EXIT_STATE_DB")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "data" / "position_exit_protection.sqlite3"


DEFAULT_STATE_DB_PATH = _default_db_path()


def tiered_profit_stop(max_net_roi: float, previous_stop: float | None = None) -> tuple[float | None, int]:
    """Return the monotonic peak-minus-three-point stop and its five-point tier."""
    try:
        peak = float(max_net_roi)
        prior = None if previous_stop is None else float(previous_stop)
    except (TypeError, ValueError, OverflowError):
        return None, 0
    if not math.isfinite(peak) or (prior is not None and not math.isfinite(prior)):
        return None, 0
    if peak < PROFIT_LOCK_START_ROI:
        return prior, 0
    tier = int((peak - PROFIT_LOCK_START_ROI) // PROFIT_LOCK_STEP_ROI) + 1
    candidate = peak - PROFIT_LOCK_DRAWDOWN_ROI
    stop = max(prior, candidate) if prior is not None else candidate
    return round(stop, 8), tier


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _position_identity(
    symbol: str, position: Mapping[str, Any],
) -> tuple[str, str, float, float] | None:
    side = str(position.get("side") or "").upper()
    opened = _finite_number(
        position.get("open_timestamp") or position.get("entry_timestamp")
    )
    entry = _finite_number(position.get("entry_price"))
    if opened is None or opened <= 0 or entry is None or entry <= 0 or side not in ("LONG", "SHORT"):
        return None
    opened_seconds = opened / 1000.0 if opened >= 1e11 else opened
    identity = f"{symbol}|{side}|{opened_seconds:.6f}|{entry:.16g}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"position-{uuid.UUID(digest[:32])}", digest, opened_seconds, entry


def _net_metrics(position: Mapping[str, Any], price: float, side: str) -> tuple[float, float] | None:
    entry = _finite_number(position.get("entry_price"))
    current = _finite_number(price)
    raw_qty = next(
        (position.get(key) for key in ("qty", "quantity", "amount")
         if position.get(key) is not None),
        None,
    )
    qty = _finite_number(raw_qty)
    if entry is None or entry <= 0 or current is None or current <= 0 or qty is None or qty == 0:
        return None

    margin = _finite_number(position.get("margin"))
    if margin is None or margin <= 0:
        leverage = _finite_number(position.get("leverage"))
        if leverage is None or leverage <= 0:
            return None
        margin = entry * abs(qty) / leverage
    if margin <= 0 or not math.isfinite(margin):
        return None

    from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
    from core.services.exits.peak_trailing_exit import estimated_display_net_pnl

    fee_rate = _finite_number(position.get("fee_rate", TAKER_FEE_RATE))
    slippage_rate = _finite_number(position.get("slippage_pct", SLIPPAGE_PCT))
    funding_value = next(
        (position.get(key) for key in ("accumulated_funding_fee", "funding_fee_usdt")
         if position.get(key) is not None),
        0.0,
    )
    funding = _finite_number(funding_value)
    if (
        fee_rate is None or fee_rate < 0
        or slippage_rate is None or slippage_rate < 0
        or funding is None
    ):
        return None

    sign = 1.0 if side == "LONG" else -1.0
    net_pnl = estimated_display_net_pnl(
        entry, current, abs(qty), sign, fee_rate, slippage_rate,
    ) - funding
    net_roi = net_pnl / margin * 100.0
    if not math.isfinite(net_pnl) or not math.isfinite(net_roi):
        return None
    return net_pnl, net_roi


def _validated_market_frame(
    frame: Any, now: float,
) -> pd.DataFrame | None:
    if not isinstance(frame, pd.DataFrame) or len(frame) < 2:
        return None
    try:
        interval = float(frame.attrs["timeframe_ms"])
        snapshot_ms = float(frame.attrs["snapshot_ms"])
        if snapshot_ms < 1e11:
            snapshot_ms *= 1000.0
        now_ms = float(now) * 1000.0
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if (
        interval != _CANDLE_INTERVAL_MS
        or not math.isfinite(snapshot_ms)
        or not math.isfinite(now_ms)
        or not 0 <= now_ms - snapshot_ms <= _MAX_SNAPSHOT_AGE_MS
    ):
        return None

    live = frame.iloc[-1]
    previous = frame.iloc[-2]
    if (
        not isinstance(live.get("is_closed"), (bool, np.bool_))
        or bool(live.get("is_closed"))
        or not isinstance(previous.get("is_closed"), (bool, np.bool_))
        or not bool(previous.get("is_closed"))
    ):
        return None
    live_stamp = _finite_number(live.get("timestamp"))
    previous_stamp = _finite_number(previous.get("timestamp"))
    if (
        live_stamp is None or previous_stamp is None
        or live_stamp - previous_stamp != interval
        or live_stamp != math.floor(now_ms / interval) * interval
        or live_stamp > snapshot_ms
        or previous_stamp + interval > snapshot_ms
    ):
        return None

    from core.services.candle_data import closed_entry_candles

    closed = closed_entry_candles(frame)
    if closed.empty:
        return closed
    for _, candle in closed.iterrows():
        values = [_finite_number(candle.get(key)) for key in ("open", "high", "low", "close")]
        if (
            any(value is None or value <= 0 for value in values)
            or values[1] < max(values[0], values[3])
            or values[2] > min(values[0], values[3])
            or values[1] < values[2]
        ):
            return None
    return closed


def _initial_state(
    position: Mapping[str, Any],
    position_uid: str,
    symbol: str,
    side: str,
    current_net_pnl: float,
    current_net_roi: float,
    closed: pd.DataFrame,
) -> dict[str, Any]:
    initial_peak_roi = max(0.0, current_net_roi)
    initial_peak_pnl = max(0.0, current_net_pnl)
    previous_peak = _finite_number(position.get("highest_net_roe"))
    if previous_peak is not None:
        initial_peak_roi = max(initial_peak_roi, previous_peak)
    lock_stop, tier = tiered_profit_stop(initial_peak_roi)
    last_candle = (
        _finite_number(closed.iloc[-1].get("timestamp"))
        if not closed.empty else None
    )
    raw_qty = next(
        (position.get(key) for key in ("qty", "quantity", "amount")
         if position.get(key) is not None),
        None,
    )
    return {
        "symbol": symbol,
        "side": side,
        "position_uid": position_uid,
        "entry_timestamp": position.get("open_timestamp") or position.get("entry_timestamp"),
        "entry_price": float(position["entry_price"]),
        "position_qty": abs(float(raw_qty)),
        "leverage": _finite_number(position.get("leverage")),
        "max_net_roi": initial_peak_roi,
        "current_net_roi": current_net_roi,
        "current_stop_roi": lock_stop,
        "profit_lock_tier": tier,
        "max_net_pnl_usdt": initial_peak_pnl,
        "current_net_pnl_usdt": current_net_pnl,
        "consecutive_opposite_candles": 0,
        "last_processed_candle_timestamp": last_candle,
        "opposite_candle_exit_triggered": False,
        "opposite_candle_exit_reason": None,
        "opposite_candle_evidence": None,
        "sequence_structure_level": None,
        "last_opposite_candle": None,
        "state_revision": 0,
    }


def _update_closed_candles(
    state: dict[str, Any],
    closed: pd.DataFrame,
    side: str,
    opened_seconds: float,
    current_net_pnl: float,
    max_net_pnl: float,
) -> None:
    if closed.empty:
        return
    last_processed = _finite_number(state.get("last_processed_candle_timestamp"))
    if last_processed is None:
        state["last_processed_candle_timestamp"] = float(closed.iloc[-1]["timestamp"])
        return

    opened_ms = opened_seconds * 1000.0
    entry_bar_end_ms = (
        math.floor(opened_ms / _CANDLE_INTERVAL_MS) + 1
    ) * _CANDLE_INTERVAL_MS
    count = int(state.get("consecutive_opposite_candles") or 0)
    sequence_level = _finite_number(state.get("sequence_structure_level"))
    last_opposite = state.get("last_opposite_candle")

    for index, (_, candle) in enumerate(closed.iterrows()):
        stamp = _finite_number(candle.get("timestamp"))
        if stamp is None or stamp <= last_processed:
            continue
        if stamp - last_processed != _CANDLE_INTERVAL_MS:
            count = 0
            sequence_level = None
            last_opposite = None
        last_processed = stamp
        state["last_processed_candle_timestamp"] = stamp

        if stamp < entry_bar_end_ms:
            count = 0
            sequence_level = None
            last_opposite = None
            continue

        opening = _finite_number(candle.get("open"))
        high = _finite_number(candle.get("high"))
        low = _finite_number(candle.get("low"))
        close = _finite_number(candle.get("close"))
        if None in (opening, high, low, close):
            count = 0
            sequence_level = None
            last_opposite = None
            continue
        is_opposite = close < opening if side == "LONG" else close > opening
        if not is_opposite:
            count = 0
            sequence_level = None
            last_opposite = None
            state["consecutive_opposite_candles"] = 0
            state["sequence_structure_level"] = None
            state["last_opposite_candle"] = None
            continue

        previous = closed.iloc[index - 1] if index > 0 else None
        previous_low = _finite_number(previous.get("low")) if previous is not None else None
        previous_high = _finite_number(previous.get("high")) if previous is not None else None
        previous_atr = _finite_number(previous.get("atr")) if previous is not None else None
        body = abs(close - opening)
        if count == 0:
            sequence_level = previous_low if side == "LONG" else previous_high
        count += 1
        current_opposite = {
            "timestamp": stamp,
            "open": opening,
            "high": high,
            "low": low,
            "close": close,
            "body": body,
        }

        strong_break = (
            count == 1
            and previous_atr is not None and previous_atr > 0
            and body >= OPPOSITE_CANDLE_STRONG_BODY_ATR * previous_atr
            and sequence_level is not None
            and (close < sequence_level if side == "LONG" else close > sequence_level)
        )
        two_candle_break = False
        if count >= OPPOSITE_CANDLE_CONFIRM_COUNT and isinstance(last_opposite, Mapping):
            prior_close = _finite_number(last_opposite.get("close"))
            ma5 = _finite_number(candle.get("ma5"))
            progressed = (
                prior_close is not None
                and (close < prior_close if side == "LONG" else close > prior_close)
            )
            structure_broken = (
                sequence_level is not None
                and (close < sequence_level if side == "LONG" else close > sequence_level)
            )
            ma5_broken = (
                ma5 is not None
                and (close < ma5 if side == "LONG" else close > ma5)
            )
            two_candle_break = progressed and (structure_broken or ma5_broken)

        if (strong_break or two_candle_break) and max_net_pnl > 0 and current_net_pnl > 0:
            state["opposite_candle_exit_triggered"] = True
            state["opposite_candle_exit_reason"] = (
                "PRIORITY_EXIT_P1_STRONG_REVERSAL_PROFIT"
                if strong_break
                else "PRIORITY_EXIT_P1_CONSECUTIVE_REVERSAL_PROFIT"
            )
            state["opposite_candle_evidence"] = {
                "trigger": "SINGLE_STRONG_CLOSED_REVERSAL" if strong_break else "TWO_CLOSED_REVERSAL_CANDLES",
                "side": side,
                "bar_timestamp": stamp,
                "first_bar_timestamp": (
                    stamp if strong_break
                    else _finite_number(last_opposite.get("timestamp"))
                ),
                "body": body,
                "reference_atr": previous_atr,
                "sequence_structure_level": sequence_level,
                "ma5": _finite_number(candle.get("ma5")),
                "close_progression_confirmed": two_candle_break,
            }
        state["consecutive_opposite_candles"] = count
        state["sequence_structure_level"] = sequence_level
        state["last_opposite_candle"] = current_opposite


def _evaluate_state(
    state: dict[str, Any],
    position: Mapping[str, Any],
    closed: pd.DataFrame,
    opened_seconds: float,
    current_net_pnl: float,
    current_net_roi: float,
) -> tuple[dict[str, Any], str | None, dict[str, Any]]:
    state["symbol"] = str(position.get("symbol") or state["symbol"])
    side = str(position.get("side") or "").upper()
    state["side"] = side
    raw_qty = next(
        (position.get(key) for key in ("qty", "quantity", "amount")
         if position.get(key) is not None),
        None,
    )
    state["position_qty"] = abs(float(raw_qty))
    state["leverage"] = _finite_number(position.get("leverage"))
    state["current_net_pnl_usdt"] = current_net_pnl
    state["current_net_roi"] = current_net_roi
    state["max_net_pnl_usdt"] = max(
        float(state.get("max_net_pnl_usdt") or 0.0), current_net_pnl,
    )
    state["max_net_roi"] = max(
        float(state.get("max_net_roi") or 0.0), current_net_roi,
    )

    previous_stop = _finite_number(state.get("current_stop_roi"))
    stop, tier = tiered_profit_stop(state["max_net_roi"], previous_stop)
    state["current_stop_roi"] = stop
    state["profit_lock_tier"] = tier

    if OPPOSITE_CANDLE_EXIT_ENABLED:
        _update_closed_candles(
            state, closed, side, opened_seconds, current_net_pnl,
            float(state["max_net_pnl_usdt"]),
        )

    if state.get("opposite_candle_exit_triggered") is True:
        reason = str(state.get("opposite_candle_exit_reason") or "")
        evidence = dict(state.get("opposite_candle_evidence") or {})
        evidence["current_net_pnl_usdt"] = current_net_pnl
        evidence["max_net_pnl_usdt"] = state["max_net_pnl_usdt"]
        evidence["current_net_roi"] = current_net_roi
        evidence["max_net_roi"] = state["max_net_roi"]
        return state, reason, evidence

    stop_value = _finite_number(state.get("current_stop_roi"))
    lock_hit = (
        stop_value is not None
        and (current_net_roi < stop_value or math.isclose(
            current_net_roi, stop_value, rel_tol=1e-9, abs_tol=1e-9,
        ))
    )
    if lock_hit:
        return state, "PRIORITY_EXIT_P1_NET_ROE_PROFIT_LOCK", {
            "trigger": "DYNAMIC_NET_ROE_LOCK",
            "current_net_pnl_usdt": current_net_pnl,
            "max_net_pnl_usdt": state["max_net_pnl_usdt"],
            "current_net_roi": current_net_roi,
            "max_net_roi": state["max_net_roi"],
            "current_stop_roi": stop_value,
            "profit_lock_tier": tier,
        }
    return state, None, {}


def evaluate_and_persist_position_protection(
    symbol: str,
    position: Mapping[str, Any],
    frame: Any,
    quote: float,
    *,
    now: float | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate protections and atomically persist position-bound state with CAS."""
    current_time = time.time() if now is None else float(now)
    side = str(position.get("side") or "").upper()
    identity = _position_identity(symbol, position)
    quote_value = _finite_number(quote)
    closed = _validated_market_frame(frame, current_time)
    if identity is None or side not in ("LONG", "SHORT") or quote_value is None or quote_value <= 0 or closed is None:
        return {"status": "SKIPPED", "reason": "POSITION_PROTECTION_INPUT_UNAVAILABLE"}

    position_uid, fingerprint, opened_seconds, _entry = identity
    saved_uid = position.get("position_uid")
    if saved_uid is not None and str(saved_uid) != position_uid:
        return {"status": "BLOCKED", "reason": "POSITION_PROTECTION_UID_MISMATCH"}

    metrics = _net_metrics(position, quote_value, side)
    if metrics is None:
        return {"status": "SKIPPED", "reason": "POSITION_PROTECTION_NET_PNL_UNAVAILABLE"}
    current_net_pnl, current_net_roi = metrics

    path = Path(db_path) if db_path is not None else DEFAULT_STATE_DB_PATH
    connection = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=2.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 2000")
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                position_uid TEXT PRIMARY KEY,
                identity_hash TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                state_json TEXT NOT NULL,
                state_revision INTEGER NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            f"SELECT identity_hash, side, state_json, state_revision "
            f"FROM {_TABLE_NAME} WHERE position_uid = ?",
            (position_uid,),
        ).fetchone()
        if row is None:
            state = _initial_state(
                position, position_uid, symbol, side, current_net_pnl,
                current_net_roi, closed,
            )
            old_revision = 0
        else:
            identity_hash, stored_side, raw_state, old_revision = row
            if identity_hash != fingerprint or stored_side != side:
                connection.rollback()
                connection.close()
                return {"status": "BLOCKED", "reason": "POSITION_PROTECTION_IDENTITY_CONFLICT"}
            try:
                state = json.loads(raw_state)
            except json.JSONDecodeError:
                connection.rollback()
                connection.close()
                logger.error("POSITION_PROTECTION_STATE_JSON_INVALID symbol=%s uid=%s", symbol, position_uid)
                return {"status": "BLOCKED", "reason": "POSITION_PROTECTION_STATE_INVALID"}
            if (
                not isinstance(state, dict)
                or int(state.get("state_revision", old_revision)) != int(old_revision)
                or state.get("position_uid") != position_uid
                or state.get("side") != side
            ):
                connection.rollback()
                connection.close()
                return {"status": "BLOCKED", "reason": "POSITION_PROTECTION_STATE_INVALID"}

        state_before = json.dumps(
            {key: value for key, value in state.items() if key != "state_revision"},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        state, reason, evidence = _evaluate_state(
            state, position, closed, opened_seconds, current_net_pnl, current_net_roi,
        )
        state_payload = {
            key: value for key, value in state.items() if key != "state_revision"
        }
        state_after = json.dumps(
            state_payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        changed = row is None or state_after != state_before
        new_revision = int(old_revision) + 1 if changed else int(old_revision)
        state["state_revision"] = new_revision
        state_json = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if row is None:
            connection.execute(
                f"INSERT INTO {_TABLE_NAME} "
                "(position_uid, identity_hash, symbol, side, state_json, state_revision, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (position_uid, fingerprint, symbol, side, state_json, new_revision, current_time),
            )
        elif changed:
            cursor = connection.execute(
                f"UPDATE {_TABLE_NAME} SET state_json = ?, state_revision = ?, updated_at = ? "
                "WHERE position_uid = ? AND state_revision = ? AND identity_hash = ?",
                (state_json, new_revision, current_time, position_uid, old_revision, fingerprint),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                connection.close()
                return {"status": "BLOCKED", "reason": "POSITION_PROTECTION_CAS_CONFLICT"}
        else:
            new_revision = int(old_revision)
            state["state_revision"] = new_revision
        connection.commit()
        connection.close()
    except (sqlite3.Error, OSError, TypeError, ValueError, OverflowError) as exc:
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                logger.exception("POSITION_PROTECTION_ROLLBACK_FAILED symbol=%s", symbol)
            finally:
                connection.close()
        logger.exception("POSITION_PROTECTION_PERSIST_FAILED symbol=%s", symbol)
        return {
            "status": "BLOCKED",
            "reason": "POSITION_PROTECTION_PERSIST_FAILED",
            "error": str(exc),
        }

    evidence.update({
        "position_uid": position_uid,
        "state_revision": new_revision,
        "current_net_pnl_usdt": current_net_pnl,
        "max_net_pnl_usdt": float(state["max_net_pnl_usdt"]),
        "current_net_roi": current_net_roi,
        "max_net_roi": float(state["max_net_roi"]),
        "current_stop_roi": state.get("current_stop_roi"),
        "profit_lock_tier": int(state.get("profit_lock_tier") or 0),
        "consecutive_opposite_candles": int(
            state.get("consecutive_opposite_candles") or 0
        ),
        "last_processed_candle_timestamp": state.get(
            "last_processed_candle_timestamp"
        ),
    })
    return {
        "status": "READY",
        "position_uid": position_uid,
        "state_revision": new_revision,
        "reason": reason,
        "evidence": evidence,
        "position_uid_created": saved_uid is None,
    }


__all__ = [
    "DEFAULT_STATE_DB_PATH",
    "OPPOSITE_CANDLE_CONFIRM_COUNT",
    "OPPOSITE_CANDLE_EXIT_ENABLED",
    "OPPOSITE_CANDLE_STRONG_BODY_ATR",
    "PROFIT_LOCK_DRAWDOWN_ROI",
    "PROFIT_LOCK_START_ROI",
    "PROFIT_LOCK_STEP_ROI",
    "evaluate_and_persist_position_protection",
    "tiered_profit_stop",
]
