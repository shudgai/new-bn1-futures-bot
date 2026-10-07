"""Shared, fresh completed-MA swing qualification; never creates order authority."""
from decimal import Decimal
import math

from core.services.candle_data import closed_entry_candles
from core.services.closed_ma_cross import closed_cross_evidence
from core.services.entry_chop_gate import evaluate_entry_chop, EVIDENCE_KEYS as CHOP_KEYS

MIN_MA5_SLOPE_ATR = Decimal("0.05")
CHOP_EXEMPT_CODES = frozenset(
    prefix+side for prefix in ("KC_LIVE_BODY_BREAKOUT_", "KC_2BAR_CONFIRM_")
    for side in ("LONG", "SHORT")
)
EVIDENCE_KEYS = CHOP_KEYS + (
    "swing_event", "swing_event_bar_ms", "swing_live_bar_ms",
    "swing_ma5_left", "swing_ma5_pivot", "swing_ma5_latest",
    "swing_reference_atr", "swing_ma5_slope_atr",
    "swing_cross_previous_ma5", "swing_cross_previous_ma15",
    "swing_cross_ma5", "swing_cross_ma15",
)


def evaluate_swing_entry_gate(frame, side, *, authority_code=None):
    status, chop = evaluate_entry_chop(
        frame, enforce_limits=authority_code not in CHOP_EXEMPT_CODES)
    if chop is None:
        return status, None
    try:
        if side not in ("LONG", "SHORT") or frame.attrs.get("timeframe_ms", 60000) != 60000:
            return "BLOCKED_SWING_ENTRY_DATA", None
        closed = closed_entry_candles(frame)
        rows = closed.iloc[-3:]
        middle = [float(value) for value in rows.ma5]
        atr = float(rows.iloc[-1].atr)
        if not all(math.isfinite(value) and value > 0 for value in (*middle, atr)):
            return "BLOCKED_SWING_ENTRY_DATA", None
        sign = 1 if side == "LONG" else -1
        move = sign*(Decimal(str(middle[2]))-Decimal(str(middle[1])))
        if move < MIN_MA5_SLOPE_ATR*Decimal(str(atr)):
            return "BLOCKED_SWING_FLAT_OR_OPPOSITE_MA5", None
        tolerance = max(middle)*1e-12
        pivot = sign*(middle[1]-middle[0]) < -tolerance
        cross = closed_cross_evidence(frame)
        cross_ready = cross is not None and cross["direction"] == side
        if not pivot and not cross_ready:
            return "WAIT_FRESH_MA5_PIVOT_OR_CROSS", None
        result = dict(
            chop, swing_event="MA5_PIVOT" if pivot else "MA5_MA15_CROSS",
            swing_event_bar_ms=float(rows.iloc[-1].timestamp),
            swing_live_bar_ms=float(frame.iloc[-1].timestamp),
            swing_ma5_left=middle[0], swing_ma5_pivot=middle[1], swing_ma5_latest=middle[2],
            swing_reference_atr=atr, swing_ma5_slope_atr=float(move/Decimal(str(atr))),
        )
        if cross_ready:
            result.update(
                swing_cross_previous_ma5=cross["previous_ma5"],
                swing_cross_previous_ma15=cross["previous_ma15"],
                swing_cross_ma5=cross["ma5"], swing_cross_ma15=cross["ma15"],
            )
        return "PASS", result
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "BLOCKED_SWING_ENTRY_DATA", None
