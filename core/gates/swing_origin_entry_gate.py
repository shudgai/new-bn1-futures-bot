"""Fresh KC swing-origin breakout and late-entry extension checks."""

from __future__ import annotations

import math

from core.services.candle_data import closed_entry_candles


LONG_SWING_ORIGIN_CODE = "KC_SWING_ORIGIN_BREAKOUT_LONG"
SHORT_SWING_ORIGIN_CODE = "KC_SWING_ORIGIN_BREAKOUT_SHORT"
SWING_ORIGIN_CODES = frozenset((LONG_SWING_ORIGIN_CODE, SHORT_SWING_ORIGIN_CODE))
SWING_ORIGIN_PHASE = "KC_SWING_ORIGIN_BREAKOUT"
SWING_ORIGIN_MAX_BIAS_ATR = 0.6
LATE_ENTRY_MAX_BIAS_ATR = 0.8


def swing_origin_evidence(frame, quote, side):
    """Return strict evidence only for the first aligned candle outside KC."""
    if side not in ("LONG", "SHORT") or frame is None or frame.empty:
        return None
    try:
        closed = closed_entry_candles(frame)
        live = frame.iloc[-1]
        is_live = not bool(live.get("is_closed", True))
        minimum_closed = 2 if is_live else 3
        if len(closed) < minimum_closed:
            return None

        current = live if is_live else closed.iloc[-1]
        previous = closed.iloc[-1] if is_live else closed.iloc[-2]
        older = closed.iloc[-2] if is_live else closed.iloc[-3]
        opening = float(current["open"])
        raw_close = float(current["close"])
        price = float(quote)
        atr = float(closed.iloc[-1]["atr"])
        current_middle = float(current["kc_middle"])
        previous_middle = float(previous["kc_middle"])
        older_middle = float(older["kc_middle"])
        current_ma5 = float(current["ma5"])
        current_ma15 = float(current["ma15"])
        previous_ma5 = float(previous["ma5"])
        upper = float(current["kc_upper"])
        lower = float(current["kc_lower"])
        adjustment = price - raw_close if is_live else 0.0
        current_ma5 += adjustment / 5.0
        current_ma15 += adjustment / 15.0
        values = (
            opening, raw_close, price, atr, current_middle, previous_middle,
            older_middle, current_ma5, current_ma15, previous_ma5, upper, lower,
        )
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or atr <= 0 or not lower < current_middle < upper):
            return None

        signed_body = price - opening if side == "LONG" else opening - price
        if signed_body <= 0:
            return None
        if side == "LONG":
            middle_turn = current_middle > previous_middle and previous_middle <= older_middle
            aligned = current_ma5 > current_ma15 and current_ma5 > previous_ma5
            rail_broken = price > upper
            bias = (price - current_ma15) / atr
        else:
            middle_turn = current_middle < previous_middle and previous_middle >= older_middle
            aligned = current_ma5 < current_ma15 and current_ma5 < previous_ma5
            rail_broken = price < lower
            bias = (current_ma15 - price) / atr

        if (middle_turn and aligned and rail_broken
                and 0.0 <= bias <= SWING_ORIGIN_MAX_BIAS_ATR):
            return {
                "side": side,
                "quote": price,
                "open": opening,
                "body": signed_body,
                "atr": atr,
                "ma15": current_ma15,
                "ma15_bias_atr": bias,
                "kc_middle_slope": current_middle - previous_middle,
                "ma5_slope": current_ma5 - previous_ma5,
                "kc_upper": upper,
                "kc_lower": lower,
                "bar_id": float(current["timestamp"]),
            }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None
    return None


def late_entry_problem(frame, quote, side):
    """Block all entries once price is more than 0.8 ATR from MA15."""
    if side not in ("LONG", "SHORT") or frame is None or frame.empty:
        return "BLOCKED_LATE_ENTRY_DATA_INVALID"
    try:
        current = frame.iloc[-1]
        closed = closed_entry_candles(frame)
        if closed.empty:
            return "BLOCKED_LATE_ENTRY_DATA_INVALID"
        price = float(quote)
        close = float(current["close"])
        ma15 = float(current["ma15"])
        if not bool(current.get("is_closed", True)):
            ma15 += (price - close) / 15.0
        atr = float(closed.iloc[-1]["atr"])
        if not all(math.isfinite(value) and value > 0 for value in (price, ma15, atr)):
            return "BLOCKED_LATE_ENTRY_DATA_INVALID"
        bias = (price - ma15) / atr if side == "LONG" else (ma15 - price) / atr
        if bias > LATE_ENTRY_MAX_BIAS_ATR and not math.isclose(
            bias, LATE_ENTRY_MAX_BIAS_ATR, rel_tol=1e-12, abs_tol=0.0,
        ):
            return "BLOCKED_LATE_LONG_CHASE" if side == "LONG" else "BLOCKED_LATE_SHORT_CHASE"
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "BLOCKED_LATE_ENTRY_DATA_INVALID"
