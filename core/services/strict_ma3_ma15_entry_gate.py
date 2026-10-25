"""Zero-trust, closed-candle MA3/MA15 entry authorization.

This module only authorizes a candidate. Callers must persist/atomically claim
``bar_time`` before submitting an order and still run account and risk gates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd
from core.services.candle_data import closed_entry_candles


@dataclass(frozen=True)
class EntryDecision:
    action: str = "HOLD / PASS"
    side: Optional[str] = None
    bar_time: Optional[int] = None
    entry_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    checklist: Dict[str, bool] = field(default_factory=dict)
    violations: List[str] = field(default_factory=list)


def _strict_true(value: Any) -> bool:
    return (
        (isinstance(value, bool) and value is True)
        or (type(value).__name__ == "bool_" and bool(value))
    )


def evaluate_ma3_ma15_entry(
    frame: pd.DataFrame,
    side: str,
    *,
    current_position: str,
    last_trade_bar_time: Optional[int],
    stop_loss_price: Optional[float],
    execution_price: Optional[float] = None,
    min_spread_ratio: float = 0.0015,
    max_adverse_wick_body_ratio: float = 0.70,
) -> EntryDecision:
    """Return OPEN_LONG/OPEN_SHORT only when every whitelist check passes.

    ``frame`` must end with the just-closed candle and include ``is_closed``.
    MA columns are used when present; otherwise simple moving averages are
    calculated from closed-candle closes. KC direction is strict: a flat
    middle line fails. ``current_position`` must explicitly be ``IDLE``.
    """
    side = str(side).upper()
    checks: Dict[str, bool] = {
        "LATEST_BAR_CLOSED": False,
        "KC_TREND_AND_MIDLINE": False,
        "STRICT_MA3_MA15_CROSS": False,
        "CANDLE_BODY_AND_WICK": False,
        "MA_SPREAD": False,
        "POSITION_IDLE_AND_BAR_UNUSED": False,
        "STOP_LOSS_VALID": False,
    }
    violations: List[str] = []

    def reject(message: str) -> EntryDecision:
        violations.append(message)
        return EntryDecision(checklist=checks.copy(), violations=violations.copy())

    if side not in ("LONG", "SHORT"):
        return reject("INVALID_SIDE")
    try:
        min_spread_ratio = float(min_spread_ratio)
        max_adverse_wick_body_ratio = float(max_adverse_wick_body_ratio)
    except (TypeError, ValueError, OverflowError):
        return reject("INVALID_WHITELIST_CONFIGURATION")
    if (not math.isfinite(min_spread_ratio) or min_spread_ratio < 0
            or not math.isfinite(max_adverse_wick_body_ratio)
            or max_adverse_wick_body_ratio < 0):
        return reject("INVALID_WHITELIST_CONFIGURATION")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return reject("MISSING_CANDLE_DATA")
    if "is_closed" not in frame.columns or not _strict_true(frame.iloc[-1]["is_closed"]):
        return reject("LATEST_CANDLE_NOT_CONFIRMED_CLOSED")

    required = {"open", "high", "low", "close", "kc_middle", "is_closed"}
    if not required.issubset(frame.columns):
        return reject("MISSING_REQUIRED_COLUMNS")

    closed = closed_entry_candles(frame)
    if len(closed) != len(frame):
        return reject("CANDLE_SNAPSHOT_CONTAINS_UNCLOSED_OR_INVALID_ROWS")
    if len(closed) < 16:
        return reject("INSUFFICIENT_CLOSED_BARS")
    checks["LATEST_BAR_CLOSED"] = True

    try:
        closes = pd.to_numeric(closed["close"], errors="coerce")
        ma3 = (pd.to_numeric(closed["ma3"], errors="coerce")
               if "ma3" in closed else closes.rolling(3, min_periods=3).mean())
        ma15 = (pd.to_numeric(closed["ma15"], errors="coerce")
                if "ma15" in closed else closes.rolling(15, min_periods=15).mean())
        previous, current = closed.iloc[-2], closed.iloc[-1]
        values = {
            "open": float(current["open"]), "high": float(current["high"]),
            "low": float(current["low"]), "close": float(current["close"]),
            "kc_now": float(current["kc_middle"]),
            "kc_prev": float(previous["kc_middle"]),
            "ma3_now": float(ma3.iloc[-1]), "ma15_now": float(ma15.iloc[-1]),
            "ma3_prev": float(ma3.iloc[-2]), "ma15_prev": float(ma15.iloc[-2]),
        }
        bar_time = int(current.get("timestamp", current.get("open_time")))
        stop = float(stop_loss_price) if stop_loss_price is not None else math.nan
        execution = float(execution_price) if execution_price is not None else float(current["close"])
    except (TypeError, ValueError, OverflowError):
        return reject("INVALID_OR_INCOMPLETE_INDICATOR_DATA")

    if (not all(math.isfinite(v) for v in values.values())
            or bar_time <= 0 or not math.isfinite(execution) or execution <= 0
            or values["ma15_now"] == 0
            or min(values["open"], values["high"], values["low"], values["close"],
                   values["kc_now"], values["kc_prev"], values["ma3_now"],
                   values["ma15_now"], values["ma3_prev"], values["ma15_prev"]) <= 0
            or values["low"] > min(values["open"], values["close"])
            or values["high"] < max(values["open"], values["close"])):
        return reject("INVALID_OR_INCOMPLETE_INDICATOR_DATA")

    if side == "LONG":
        kc_ok = values["kc_now"] > values["kc_prev"] and values["close"] > values["kc_now"]
        cross_ok = values["ma3_prev"] <= values["ma15_prev"] and values["ma3_now"] > values["ma15_now"]
        body_ok = values["close"] > values["open"]
        adverse_wick = values["high"] - max(values["open"], values["close"])
        action = "OPEN_LONG"
    else:
        kc_ok = values["kc_now"] < values["kc_prev"] and values["close"] < values["kc_now"]
        cross_ok = values["ma3_prev"] >= values["ma15_prev"] and values["ma3_now"] < values["ma15_now"]
        body_ok = values["close"] < values["open"]
        adverse_wick = min(values["open"], values["close"]) - values["low"]

    checks["KC_TREND_AND_MIDLINE"] = kc_ok
    checks["STRICT_MA3_MA15_CROSS"] = cross_ok
    body = abs(values["close"] - values["open"])
    checks["CANDLE_BODY_AND_WICK"] = (
        body_ok and body > 0 and adverse_wick <= max_adverse_wick_body_ratio * body
    )
    checks["MA_SPREAD"] = (
        abs(values["ma3_now"] - values["ma15_now"]) / abs(values["ma15_now"])
        >= min_spread_ratio
    )
    try:
        bar_unused = last_trade_bar_time is None or int(last_trade_bar_time) != bar_time
    except (TypeError, ValueError, OverflowError):
        bar_unused = False
    checks["POSITION_IDLE_AND_BAR_UNUSED"] = (
        str(current_position).upper() == "IDLE" and bar_unused
    )
    checks["STOP_LOSS_VALID"] = (
        math.isfinite(stop)
        and (stop < execution if side == "LONG" else stop > execution)
    )

    labels = {
        "LATEST_BAR_CLOSED": "LATEST_CANDLE_NOT_CONFIRMED_CLOSED",
        "KC_TREND_AND_MIDLINE": "KC_DIRECTION_OR_MIDLINE_CONFLICT",
        "STRICT_MA3_MA15_CROSS": "NO_STRICT_MA3_MA15_CROSS",
        "CANDLE_BODY_AND_WICK": "CANDLE_BODY_OR_ADVERSE_WICK_FAILED",
        "MA_SPREAD": "MA_SPREAD_BELOW_THRESHOLD",
        "POSITION_IDLE_AND_BAR_UNUSED": "POSITION_NOT_IDLE_OR_BAR_ALREADY_TRADED",
        "STOP_LOSS_VALID": "STOP_LOSS_MISSING_OR_ON_WRONG_SIDE",
    }
    violations = [labels[key] for key, passed in checks.items() if not passed]
    if violations:
        return EntryDecision(checklist=checks, violations=violations)

    return EntryDecision(
        action=action,
        side=side,
        bar_time=bar_time,
        entry_price=execution,
        stop_loss_price=stop,
        checklist=checks,
        violations=[],
    )
