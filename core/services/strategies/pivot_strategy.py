"""Closed price/MA3 pivots with first-turn confirmation and exit state.
Implements IEntryStrategy interface.
"""
import math
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy

PIVOT_CODES = {"KC_MA15_TROUGH_LONG", "KC_MA15_PEAK_SHORT"}


def closed_ck_direction(frame, require_outer_slope=True):
    """Use three closed midpoints and a non-adverse directional outer rail."""
    try:
        if frame is None or len(frame) < 4:
            return None
        middle_key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
        rows = [frame.iloc[i] for i in (-4, -3, -2)]
        middle = [float(r[middle_key]) for r in rows]
        upper = [float(r["kc_upper"]) for r in rows]
        lower = [float(r["kc_lower"]) for r in rows]
        if not all(math.isfinite(v) and v > 0 for v in middle + upper + lower):
            return None
        if any(lo >= hi for lo, hi in zip(lower, upper)):
            return None
        if middle[0] < middle[1] < middle[2] and (not require_outer_slope or upper[0] <= upper[1] <= upper[2]):
            return "LONG"
        if middle[0] > middle[1] > middle[2] and (not require_outer_slope or lower[0] >= lower[1] >= lower[2]):
            return "SHORT"
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return None
    return None


def pivot_entry(frame, price):
    wait = {"action": "WAIT", "side": None, "reason": "WAIT_MA15_PRICE_PIVOT"}
    required = {"open", "high", "low", "close", "ma3", "kc_upper", "kc_lower"}
    if frame is None or len(frame) < 4 or not required.issubset(frame.columns):
        return {**wait, "reason": "KC_DATA_UNAVAILABLE"}
    try:
        left, pivot, right = (frame.iloc[-4], frame.iloc[-3], frame.iloc[-2])
        rows = (left, pivot, right)
        values = [float(row[key]) for row in rows for key in required] + [float(price)]
        if not all(math.isfinite(v) and v > 0 for v in values):
            return {**wait, "reason": "KC_DATA_INVALID"}
        if any(not (float(r["low"]) <= min(float(r["open"]), float(r["close"]))
                    <= max(float(r["open"]), float(r["close"])) <= float(r["high"])) for r in rows):
            return {**wait, "reason": "KC_DATA_INVALID"}
        rails = [(float(r["kc_lower"]), float(r["kc_upper"])) for r in (*rows, frame.iloc[-1])]
        if any(not (math.isfinite(lo) and math.isfinite(hi) and 0 < lo < hi) for lo, hi in rails):
            return {**wait, "reason": "KC_DATA_INVALID"}
        middle_key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
        trend_rows = rows
        middle = [float(r[middle_key]) for r in trend_rows]
        if not all(math.isfinite(v) and v > 0 for v in middle):
            return {**wait, "reason": "KC_DATA_INVALID"}
        direction = closed_ck_direction(frame)

        live = frame.iloc[-1]
        ma3_live = float(live.get("ma3", 0.0))
        ma3_right = float(right["ma3"])

        if (float(pivot["low"]) < min(float(left["low"]), float(right["low"]))
                and float(right["close"]) > float(right["open"])
                and float(left["ma3"]) > float(pivot["ma3"]) < ma3_right
                and ma3_live > ma3_right
                and price > float(pivot["low"])):
            if direction != "LONG":
                return {**wait, "reason": "KC_DIRECTION_BLOCK_LONG"}
            return {"action": "ENTER", "side": "LONG", "reason": "KC_MA15_TROUGH_LONG"}
        if (float(pivot["high"]) > max(float(left["high"]), float(right["high"]))
                and float(right["close"]) < float(right["open"])
                and float(left["ma3"]) < float(pivot["ma3"]) > ma3_right
                and ma3_live < ma3_right
                and price < float(pivot["high"])):
            if direction != "SHORT":
                return {**wait, "reason": "KC_DIRECTION_BLOCK_SHORT"}
            return {"action": "ENTER", "side": "SHORT", "reason": "KC_MA15_PEAK_SHORT"}
    except (TypeError, ValueError, KeyError, IndexError):
        return {**wait, "reason": "KC_DATA_INVALID"}
    return wait


def pivot_middle_exit(position, price, middle):
    """Persist favorable crossing and latch a failed close for the next scan."""
    if not position.get("channel_pivot_entry"):
        return True
    if position.get("channel_pivot_middle_exit_pending"):
        return True
    side = position.get("side")
    if side not in ("LONG", "SHORT"):
        return False
    direction = 1 if side == "LONG" else -1
    try:
        if not all(math.isfinite(float(v)) and float(v) > 0 for v in (price, middle)):
            return False
        entry = float(position.get("entry_price") or 0)
        entry_middle = float(position.get("entry_kc_middle") or 0)
        if (entry > 0 and entry_middle > 0 and math.isfinite(entry) and math.isfinite(entry_middle)
                and direction * (entry - entry_middle) > 0):
            position["channel_pivot_middle_reached"] = True
        if direction * (price - middle) > 0:
            position["channel_pivot_middle_reached"] = True
        if position.get("channel_pivot_middle_reached") and direction * (price - middle) <= 0:
            position["channel_pivot_middle_exit_pending"] = True
            return True
    except (TypeError, ValueError):
        return False
    return False


class PivotChannelEntryStrategy(IEntryStrategy):
    """OOP Strategy class implementing IEntryStrategy for pivot entry evaluation."""

    def evaluate_entry(
        self,
        frame: pd.DataFrame,
        price: float,
        side: str,
        **kwargs: Any
    ) -> Tuple[bool, str, Dict[str, Any]]:
        decision = pivot_entry(frame, price)
        if decision.get("action") == "ENTER" and decision.get("side") == side:
            return True, decision.get("reason", "OK"), decision
        return False, decision.get("reason", "WAIT"), decision
