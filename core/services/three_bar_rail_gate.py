"""Fail-closed three-bar KC rail gate shared by every entry authority."""
import math

from core.services.candle_data import closed_entry_candles


def three_bar_rail_gate_problem(frame, quote, side):
    """Return a rejection reason unless the latest three bars satisfy rail rules.

    Bar 1 and Bar 2 must be completed candles. Bar 3 must be the active candle;
    its body is measured against the current quote and Bar 2's completed ATR.
    """
    try:
        if side not in ("LONG", "SHORT") or frame is None or frame.empty:
            return "BLOCKED_THREE_BAR_GATE_INVALID_DATA"
        closed = closed_entry_candles(frame)
        forming = "is_closed" in frame.columns and not bool(frame.iloc[-1]["is_closed"])
        if not forming:
            return "BLOCKED_THREE_BAR_GATE_BAR3_NOT_LIVE"
        if len(closed) < 2:
            return "BLOCKED_THREE_BAR_GATE_INSUFFICIENT_BARS"
        first, second = closed.iloc[-2], closed.iloc[-1]
        third = frame.iloc[-1]
        stamp_delta = float(third["timestamp"]) - float(second["timestamp"])
        if stamp_delta != 60000:
            return "BLOCKED_THREE_BAR_GATE_BAR_GAP"

        q = float(quote)
        o1, c1 = float(first["open"]), float(first["close"])
        o2, c2 = float(second["open"]), float(second["close"])
        o3 = float(third["open"])
        c3 = q
        rail = float(first["kc_upper"] if side == "LONG" else first["kc_lower"])
        atr = float(second["atr"])
        h1, l1 = float(first["high"]), float(first["low"])
        h2, l2 = float(second["high"]), float(second["low"])
        h3, l3 = max(float(third["high"]), c3), min(float(third["low"]), c3)
        vals = (q, o1, c1, h1, l1, o2, c2, h2, l2, o3, c3,
                h3, l3, rail, atr)
        if not all(math.isfinite(v) and v > 0 for v in vals) or atr <= 0:
            return "BLOCKED_THREE_BAR_GATE_INVALID_DATA"
        if (h1 < max(o1, c1) or l1 > min(o1, c1)
                or h2 < max(o2, c2) or l2 > min(o2, c2)
                or h3 < max(o3, c3) or l3 > min(o3, c3)):
            return "BLOCKED_THREE_BAR_GATE_INVALID_DATA"
        latest_rail = float(third["kc_upper"] if side == "LONG" else third["kc_lower"])
        if not math.isfinite(latest_rail) or latest_rail <= 0:
            return "BLOCKED_THREE_BAR_GATE_INVALID_DATA"
        if (side == "LONG" and q <= latest_rail) or (side == "SHORT" and q >= latest_rail):
            return f"BLOCKED_THREE_BAR_{side}_QUOTE_RETURNED_INSIDE_RAIL"

        if side == "LONG":
            if not c1 > float(first["kc_upper"]):
                return "BLOCKED_THREE_BAR_LONG_NOT_BROKEN_UPPER_RAIL"
            if c1 <= o1 or c2 <= o2:
                return "BLOCKED_THREE_BAR_LONG_BAR2_NOT_BULLISH"
            body3 = c3 - o3
            if body3 >= 0:
                return None
            ma_values = []
            live_close = float(third["close"])
            for key in ("ma3", "ma5"):
                if key in third and third[key] is not None:
                    value = float(third[key])
                    if math.isfinite(value) and value > 0:
                        period = 3.0 if key == "ma3" else 5.0
                        ma_values.append(value + (q - live_close) / period)
            if len(ma_values) != 2 or not math.isfinite(live_close) or live_close <= 0:
                return "BLOCKED_THREE_BAR_GATE_INVALID_DATA"
            engulf = c3 <= o2 and o3 >= c2
            if abs(body3) / atr >= 0.4 or (ma_values and c3 < max(ma_values)) or engulf:
                return "BLOCKED_THREE_BAR_LONG_STRONG_REVERSAL"
            if abs(body3) / atr >= 0.3:
                return "BLOCKED_THREE_BAR_LONG_REVERSAL_NOT_WEAK"
            return None

        if not c1 < float(first["kc_lower"]):
            return "BLOCKED_THREE_BAR_SHORT_NOT_BROKEN_LOWER_RAIL"
        if c1 >= o1 or c2 >= o2:
            return "BLOCKED_THREE_BAR_SHORT_BAR2_NOT_BEARISH"
        body3 = c3 - o3
        if body3 <= 0:
            return None
        ratio = body3 / atr
        ma_values = []
        live_close = float(third["close"])
        for key in ("ma3", "ma5"):
            if key in third and third[key] is not None:
                value = float(third[key])
                if math.isfinite(value) and value > 0:
                    period = 3.0 if key == "ma3" else 5.0
                    ma_values.append(value + (q - live_close) / period)
        if len(ma_values) != 2 or not math.isfinite(live_close) or live_close <= 0:
            return "BLOCKED_THREE_BAR_GATE_INVALID_DATA"
        engulf = c3 >= o2 and o3 <= c2
        strong = ratio >= 0.4 or (ma_values and c3 > min(ma_values)) or engulf
        if strong:
            return "BLOCKED_THREE_BAR_SHORT_STRONG_REVERSAL"
        if ratio >= 0.3 or engulf:
            return "BLOCKED_THREE_BAR_SHORT_REVERSAL_NOT_WEAK"
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "BLOCKED_THREE_BAR_GATE_INVALID_DATA"
