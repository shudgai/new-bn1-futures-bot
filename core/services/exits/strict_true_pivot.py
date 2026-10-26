"""Strict closed-candle peak and valley confirmations."""

import math
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from core.services.candle_data import closed_entry_candles

CONFIRMED_PIVOT_PENDING_KEY = "confirmed_true_pivot_exit_pending"
_CONFIRMED_PIVOT_REASONS = {
    "LONG": "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT",
    "SHORT": "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT",
}


def _position_identity(position: Dict[str, Any]) -> Optional[list]:
    try:
        side = str(position.get("side", "")).upper()
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
        entry_snapshot = position.get("entry_snapshot")
        if opened_value is None and isinstance(entry_snapshot, dict):
            opened_value = entry_snapshot.get(
                "entry_bar_id", entry_snapshot.get("timestamp")
            )
        opened = float(opened_value or 0.0)
        entry = float(position.get("entry_price") or 0.0)
        quantity_value = (
            position.get("qty")
            or position.get("quantity")
            or position.get("amount")
        )
        quantity = abs(float(quantity_value)) if quantity_value is not None else 0.0
        if opened >= 1e11:
            opened /= 1000.0
        if (side not in _CONFIRMED_PIVOT_REASONS
                or not math.isfinite(opened)
                or not math.isfinite(entry)
                or not math.isfinite(quantity)
                or opened <= 0
                or entry <= 0
                or quantity < 0):
            return None
        return [side, opened, entry, quantity]
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def pending_confirmed_pivot_exit(
    position: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    identity = _position_identity(position)
    if identity is None:
        return None
    sources = (position, metadata) if isinstance(metadata, dict) else (position,)
    for source in sources:
        ticket = source.get(CONFIRMED_PIVOT_PENDING_KEY)
        if (not isinstance(ticket, dict)
                or ticket.get("identity") != identity
                or ticket.get("reason") != _CONFIRMED_PIVOT_REASONS[identity[0]]):
            continue
        details = ticket.get("details")
        if not isinstance(details, dict):
            continue
        try:
            pivot_ms = float(details["pivot_bar_id"])
            confirm_ms = float(details["confirm_bar_id"])
            if (not math.isfinite(pivot_ms) or not math.isfinite(confirm_ms)
                    or pivot_ms <= 0 or confirm_ms <= pivot_ms):
                continue
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        return ticket["reason"], dict(details)
    return None


def store_confirmed_pivot_exit(
    position: Dict[str, Any],
    pivot: Tuple[str, Dict[str, Any]],
) -> bool:
    identity = _position_identity(position)
    reason, details = pivot
    if (identity is None
            or reason != _CONFIRMED_PIVOT_REASONS[identity[0]]
            or not isinstance(details, dict)):
        return False
    position[CONFIRMED_PIVOT_PENDING_KEY] = {
        "identity": identity,
        "reason": reason,
        "details": dict(details),
    }
    return True


def analyze_market_structure(
    frame: Optional[pd.DataFrame],
    position_side: str,
) -> str:
    """Classify a completed five-bar structure without using KC levels."""
    if frame is None or frame.empty:
        return "HOLD"
    try:
        side = str(position_side).upper()
        if side not in ("LONG", "SHORT"):
            return "HOLD"
        closed = closed_entry_candles(frame)
        if len(closed) < 15:
            return "HOLD"
        bars = closed.iloc[-3:]
        if not {"open", "high", "low", "close", "ma5", "ma15"}.issubset(
            bars.columns
        ):
            return "HOLD"

        candles = []
        for _, row in bars.iterrows():
            values = tuple(float(row[name]) for name in ("open", "high", "low", "close"))
            if (
                not all(math.isfinite(value) and value > 0 for value in values)
                or values[1] < max(values[0], values[3])
                or values[2] > min(values[0], values[3])
                or values[2] > values[1]
            ):
                return "HOLD"
            candles.append(values)
        ma5 = [float(value) for value in bars["ma5"]]
        ma15 = [float(value) for value in bars["ma15"]]
        if not all(
            math.isfinite(value) and value > 0
            for value in ma5 + ma15
        ):
            return "HOLD"

        c0, c1, c2 = candles
        ma5_slope = ma5[-1] - ma5[-2]
        if side == "LONG":
            if (
                c1[1] > c0[1]
                and c1[1] > c2[1]
                and c2[3] < min(c1[0], c1[3])
                and ma5_slope < 0
            ):
                return "TRUE_PEAK"
            if c2[3] >= ma15[-1] and ma5[-1] > ma15[-1]:
                return "PULLBACK"
        else:
            if (
                c1[2] < c0[2]
                and c1[2] < c2[2]
                and c2[3] > max(c1[0], c1[3])
                and ma5_slope > 0
            ):
                return "TRUE_TROUGH"
            if c2[3] <= ma15[-1] and ma5[-1] < ma15[-1]:
                return "PULLBACK"
        return "HOLD"
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "HOLD"


def confirmed_true_pivot_exit(
    position: Dict[str, Any],
    frame: Optional[pd.DataFrame],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return an exit when a three-bar completed true peak or trough confirms."""
    if not isinstance(position, dict) or frame is None or frame.empty:
        return None
    try:
        side = str(position.get("side", "")).upper()
        if side not in ("LONG", "SHORT"):
            return None

        closed = closed_entry_candles(frame)
        if len(closed) < 15:
            return None
        bars = closed.iloc[-3:]

        timeframe_ms = float(frame.attrs.get("timeframe_ms", 60_000) or 60_000)
        timestamps = [float(value) for value in bars["timestamp"]]
        if (
            not math.isfinite(timeframe_ms)
            or timeframe_ms <= 0
            or any(not math.isfinite(value) or value <= 0 for value in timestamps)
            or any(
                right - left != timeframe_ms
                for left, right in zip(timestamps, timestamps[1:])
            )
            or not {"ma5", "ma15"}.issubset(bars.columns)
        ):
            return None

        entry_timestamp = None
        entry_snapshot = position.get("entry_snapshot")
        for key in ("open_timestamp", "entry_timestamp", "open_bar_id", "entry_bar_id"):
            value = position.get(key)
            if value is not None:
                entry_timestamp = float(value)
                break
        if entry_timestamp is None and isinstance(entry_snapshot, dict):
            value = entry_snapshot.get("entry_bar_id", entry_snapshot.get("timestamp"))
            if value is not None:
                entry_timestamp = float(value)
        if entry_timestamp is None or not math.isfinite(entry_timestamp) or entry_timestamp <= 0:
            return None

        entry_ms = entry_timestamp * 1000.0 if entry_timestamp < 1e11 else entry_timestamp
        entry_bar_ms = math.floor(entry_ms / timeframe_ms) * timeframe_ms
        pivot_ms = timestamps[1]
        confirmation_ms = timestamps[2]
        if pivot_ms <= entry_bar_ms or confirmation_ms <= entry_bar_ms:
            return None

        structure = analyze_market_structure(frame, side)
        if structure not in ("TRUE_PEAK", "TRUE_TROUGH"):
            return None
        pivot = bars.iloc[1]
        confirmation = bars.iloc[2]
        entry_price = float(position.get("entry_price") or position.get("price") or 0.0)
        atr = float(confirmation.get("atr") or bars.iloc[-1].get("atr") or 0.0)
        if atr <= 0:
            atr = abs(float(confirmation["high"]) - float(confirmation["low"]))
        peak_roe = float(position.get("peak_roe", 0.0) or 0.0)
        ma5_slope = float(confirmation["ma5"]) - float(bars.iloc[1]["ma5"])
        conf_close = float(confirmation["close"])
        kc_mid = float(confirmation.get("kc_middle", 0.0))
        conf_ma15 = float(confirmation.get("ma15", 0.0))

        if side == "LONG":
            pivot_high = float(pivot["high"])
            gain = pivot_high - entry_price if entry_price > 0 else 0.0
            # 【方案 A 波段獲利過濾鐵律】：
            # 必須實質脫離成本區間 (gain >= 0.5 * ATR 或 peak_roe >= 0.05)，真峰頂才具備平倉獲利了結資格！
            # 若波段未展開（未達 0.5 ATR 且 peak_roe < 5%），100% 判定為「成本區間正常回踩」，絕不平多！
            if entry_price > 0 and (gain < 0.5 * atr and peak_roe < 0.05):
                return None

            return "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", {
                "pivot_bar_id": pivot_ms,
                "confirm_bar_id": confirmation_ms,
                "pivot_high": pivot_high,
                "confirmation_close": conf_close,
                "ma5_slope": ma5_slope,
            }

        pivot_low = float(pivot["low"])
        drop = entry_price - pivot_low if entry_price > 0 else 0.0
        # 空單同理：若未實質探底展開 (drop < 0.5 * ATR 且 peak_roe < 5%)，100% 判定為正常反彈，絕不平空！
        if entry_price > 0 and (drop < 0.5 * atr and peak_roe < 0.05):
            return None

        return "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT", {
            "pivot_bar_id": pivot_ms,
            "confirm_bar_id": confirmation_ms,
            "pivot_low": pivot_low,
            "confirmation_close": conf_close,
            "ma5_slope": ma5_slope,
        }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None
