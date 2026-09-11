"""Confirmed CK trend entries; legacy rail helpers remain for exit compatibility.
Implements IEntryStrategy interface.
"""
import math
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy

LIVE_BODY_BREAKOUT_CODES = {"KC_LIVE_BODY_BREAKOUT_LONG", "KC_LIVE_BODY_BREAKOUT_SHORT"}
LIVE_OUTER_CODES = {"KC_LIVE_OUTER_LONG", "KC_LIVE_OUTER_SHORT"} | LIVE_BODY_BREAKOUT_CODES
LIVE_BREAKOUT_BODY_ATR = 0.5
OUTER_CODES = {"KC_OUTSIDE_LONG", "KC_OUTSIDE_SHORT"}
ENTRY_TREND_CODES = {"KC_TREND_LONG", "KC_TREND_SHORT"}
TREND_CODES = {"KC_MIDDLE_TREND_LONG", "KC_MIDDLE_TREND_SHORT"} | ENTRY_TREND_CODES


def ck_momentum_fading(frame, side):
    """Return closed-bar directional fading, or None for invalid data."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 5:
            return None
        key = 'kc_middle' if 'kc_middle' in frame.columns else 'ema_20'
        values = [float(v) for v in frame.iloc[-5:-1][key]]
        if not all(math.isfinite(v) and v > 0 for v in values):
            return None
        sign = 1 if side == 'LONG' else -1
        steps = [sign * (b - a) for a, b in zip(values, values[1:])]
        tolerance = max(values) * 1e-12
        fading = (steps[2] > 0 and steps[0] - steps[1] > tolerance
                  and steps[1] - steps[2] > tolerance)
        return fading
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return None


def ck_entry_momentum_ready(frame, side):
    """Only strengthening confirmed directional CK momentum permits entries."""
    try:
        if ck_momentum_fading(frame, side) is not False:
            return False
        key = 'kc_middle' if 'kc_middle' in frame.columns else 'ema_20'
        a, b, c = [float(v) for v in frame.iloc[-4:-1][key]]
        sign = 1 if side == 'LONG' else -1
        previous, latest = sign * (b - a), sign * (c - b)
        tolerance = max(a, b, c) * 1e-12
        return previous >= 0 and latest > 0 and latest - previous > tolerance
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def aligned_direction(frame, side):
    """CK sets direction without waiting for moving-average alignment."""
    return side in ('LONG', 'SHORT') and ck_direction(frame) == side


def ck_direction(frame):
    """Latest closed middle slope, confirmed by the directional outer slope."""
    try:
        if frame is None or len(frame) < 3:
            return None
        key = 'kc_middle' if 'kc_middle' in frame.columns else 'ema_20'
        rows = [[float(row[k]) for k in ('kc_lower', key, 'kc_upper')]
                for _, row in frame.iloc[-3:-1].iterrows()]
        if any(not all(math.isfinite(v) and v > 0 for v in row)
               or not row[0] < row[1] < row[2] for row in rows):
            return None
        a, b = rows
        tolerance = max(a[1], b[1]) * 1e-12
        if b[1] - a[1] > tolerance:
            return 'LONG'
        if a[1] - b[1] > tolerance:
            return 'SHORT'
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return None
    return None


def live_adverse_entry_safe(frame, price, side):
    """Reject adverse entry bodies at the existing ATR abnormality threshold."""
    from core.config import RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR
    try:
        opened = float(frame.iloc[-1]['open'])
        atr = float(frame.iloc[-2]['atr'])
        price = float(price)
        threshold = atr * RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR
        if (side not in ('LONG', 'SHORT')
                or not all(math.isfinite(v) and v > 0 for v in (opened, price, atr, threshold))):
            return False
        adverse_body = (1 if side == 'SHORT' else -1) * (price - opened)
        return adverse_body < threshold
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def sustained_trend_ready(frame, side):
    """Six closed candles must show persistent progress rather than oscillation."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 7:
            return False
        rows = frame.iloc[-7:-1]
        values = [[float(row[k]) for k in ('open', 'high', 'low', 'close', 'kc_middle')]
                  for _, row in rows.iterrows()]
        if not all(math.isfinite(v) and v > 0 for row in values for v in row):
            return False
        if any(not low <= min(o, c) <= max(o, c) <= high for o, high, low, c, m in values):
            return False
        sign = 1 if side == 'LONG' else -1
        if not all(sign * (b[4] - a[4]) > 0 for a, b in zip(values, values[1:])):
            return False
        progress = [sign * (b[1] - a[1]) > 0 and sign * (b[2] - a[2]) > 0
                    for a, b in zip(values, values[1:])]
        if sum(progress) < 4 or not all(progress[-2:]):
            return False
        moves = [b[3] - a[3] for a, b in zip(values, values[1:])]
        distance = sum(abs(v) for v in moves)
        if distance <= 0 or sign * sum(moves) / distance < .70:
            return False
        sides = [1 if c > m else -1 if c < m else 0 for o, h, l, c, m in values]
        nonzero = [v for v in sides if v]
        if sum(a != b for a, b in zip(nonzero, nonzero[1:])) > 1:
            return False
        overlaps = []
        for a, b in zip(values, values[1:]):
            lo_a, hi_a = sorted((a[0], a[3]))
            lo_b, hi_b = sorted((b[0], b[3]))
            smaller = min(hi_a - lo_a, hi_b - lo_b)
            overlaps.append(max(0., min(hi_a, hi_b) - max(lo_a, lo_b)) / smaller if smaller > 0 else 1.)
        return sum(overlaps) / len(overlaps) <= .50
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def ma3_outer_cross_ready(frame, price, side):
    """Cross from the last closed MA3/rail to quote-derived live MA3/rail."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 4:
            return False
        closes = [float(v) for v in frame['close'].iloc[-4:-1]]
        price = float(price)
        rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
        previous = float(frame.iloc[-2][rail])
        current = float(frame.iloc[-1][rail])
        if not all(math.isfinite(v) and v > 0 for v in [price, previous, current, *closes]):
            return False
        closed_ma = sum(closes) / 3.
        live_ma = (sum(closes[-2:]) + price) / 3.
        sign = 1 if side == 'LONG' else -1
        return (sign * (closed_ma - previous) <= 0
                and sign * (live_ma - current) > 0
                and sign * (price - current) > 0)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        return False


def ma3_outer_continuation_ready(frame, price, side):
    """A missed cross stays eligible while live MA3 and price remain outside."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 4:
            return False
        closes = [float(v) for v in frame['close'].iloc[-3:-1]]
        rail = float(frame.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
        price = float(price)
        if not all(math.isfinite(v) and v > 0 for v in [price, rail, *closes]):
            return False
        sign = 1 if side == 'LONG' else -1
        ma3 = (sum(closes) + price) / 3.
        return sign * (ma3 - rail) > 0 and sign * (price - rail) > 0
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        return False


def entry_trend_direction(frame):
    """Entry direction uses only the last two completed CK middle values."""
    try:
        if frame is None or len(frame) < 3:
            return None
        key = 'kc_middle' if 'kc_middle' in frame.columns else 'ema_20'
        previous, latest = [float(v) for v in frame[key].iloc[-3:-1]]
        if not all(math.isfinite(v) and v > 0 for v in (previous, latest)):
            return None
        return 'LONG' if latest > previous else 'SHORT' if latest < previous else None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def live_body_breakout_side(frame, price):
    """Current real body crosses an outer rail by quote, sized on closed ATR."""
    try:
        if frame is None or len(frame) < 2:
            return None
        row = frame.iloc[-1]
        opened, upper, lower, atr, price = (
            float(row['open']), float(row['kc_upper']), float(row['kc_lower']),
            float(frame.iloc[-2]['atr']), float(price))
        if (not all(math.isfinite(v) and v > 0 for v in (opened, upper, lower, atr, price))
                or lower >= upper):
            return None
        threshold = atr * LIVE_BREAKOUT_BODY_ATR
        if opened <= upper < price and price - opened >= threshold:
            return 'LONG'
        if price < lower <= opened and opened - price >= threshold:
            return 'SHORT'
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        pass
    return None


def aligned_entry(frame, price):
    """Live long-body breaks may precede CK confirmation; retain trend entries."""
    wait = {"action": "WAIT", "side": None, "reason": "KC_DIRECTION_WAIT"}
    try:
        price = float(price)
        if not math.isfinite(price) or price <= 0 or frame is None or len(frame) < 4:
            return wait
        for _, row in frame.iloc[-4:].iterrows():
            opened, high, low, closed = (float(row[k]) for k in ("open", "high", "low", "close"))
            if (not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed))
                    or not low <= min(opened, closed) <= max(opened, closed) <= high):
                return wait
        breakout_side = live_body_breakout_side(frame, price)
        side = breakout_side or entry_trend_direction(frame)
        if side is None:
            return wait
            
        if not breakout_side:
            try:
                live_ma3 = float(frame.iloc[-1]['ma3'])
                live_ma15 = float(frame.iloc[-1]['ma15'])
                if side == 'LONG' and (price < live_ma3 or price < live_ma15):
                    return {**wait, "reason": "KC_MA_SAFETY_WAIT"}
                if side == 'SHORT' and (price > live_ma3 or price > live_ma15):
                    return {**wait, "reason": "KC_MA_SAFETY_WAIT"}
            except (KeyError, ValueError, TypeError, IndexError):
                pass

            if not live_candle_color_ready(frame, price, side):
                return {**wait, "reason": "KC_LIVE_COLOR_WAIT"}

        if ck_momentum_fading(frame, side):
            return {**wait, "reason": "KC_MOMENTUM_FADING_WAIT"}
        if not live_adverse_entry_safe(frame, price, side):
            return {**wait, "reason": "KC_LIVE_ADVERSE_ENTRY_WAIT"}
        reason = ('KC_LIVE_BODY_BREAKOUT_' if breakout_side else 'KC_TREND_') + side
        return {"action": "ENTER", "side": side, "reason": reason}
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return wait


def aligned_entry_ready(frame, price, side):
    return side in ('LONG', 'SHORT') and aligned_entry(frame, price).get('side') == side


def live_ma3_direction_ready(frame, price, side):
    """Compare quote-derived live MA3 with closed MA3, ignoring stale live rows."""
    try:
        closes = [float(v) for v in frame["close"].iloc[-4:-1]]
        price = float(price)
        if (side not in ("LONG", "SHORT") or len(closes) != 3
                or not all(math.isfinite(v) and v > 0 for v in [price, *closes])):
            return False
        return (1 if side == "LONG" else -1) * (price - closes[0]) > 0
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def live_candle_color_ready(frame, price, side):
    """Require a green live body for longs and a red live body for shorts."""
    try:
        if side not in ("LONG", "SHORT") or frame is None or frame.empty:
            return False
        opened = float(frame.iloc[-1]["open"])
        price = float(price)
        if not all(math.isfinite(value) and value > 0 for value in (opened, price)):
            return False
        return (1 if side == "LONG" else -1) * (price - opened) >= 0
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def two_closed_bodies_ready(frame, side):
    """Require two completed directional bodies; the live candle never counts."""
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 3:
            return False
        sign = 1 if side == "LONG" else -1
        for _, row in frame.iloc[-3:-1].iterrows():
            opened, high, low, closed = (float(row[k]) for k in ("open", "high", "low", "close"))
            if not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed)):
                return False
            if not low <= min(opened, closed) <= max(opened, closed) <= high or high <= low:
                return False
            if sign * (closed - opened) <= 0 or abs(closed - opened) / (high - low) < .20:
                return False
        return True
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def three_closed_short_breakout_ready(frame, price):
    """Allow a small red middle candle between solid closed breakout/confirmation."""
    try:
        if frame is None or len(frame) < 4:
            return False
        rows = [frame.iloc[i] for i in (-4, -3, -2)]
        for index, row in enumerate(rows):
            opened, high, low, closed = (float(row[k]) for k in ("open", "high", "low", "close"))
            lower, upper = float(row["kc_lower"]), float(row["kc_upper"])
            if not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed, lower, upper)):
                return False
            if not (low <= closed < opened <= high and lower < upper and closed < lower):
                return False
            if index != 1 and (opened - closed) / (high - low) < .20:
                return False
        first = rows[0]
        live = frame.iloc[-1]
        lower, upper, price = float(live["kc_lower"]), float(live["kc_upper"]), float(price)
        return (float(first["open"]) >= float(first["kc_lower"])
                and all(math.isfinite(v) for v in (lower, upper, price))
                and 0 < price < lower < upper)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def confirmed_outer_continuation_ready(frame, price, side):
    """A missed breakout may continue; both closed bodies must finish outside."""
    if not two_closed_bodies_ready(frame, side):
        return False
    try:
        sign = 1 if side == "LONG" else -1
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        price = float(price)
        if not math.isfinite(price) or price <= 0:
            return False
        for offset in (-3, -2, -1):
            row = frame.iloc[offset]
            lower, upper = float(row["kc_lower"]), float(row["kc_upper"])
            if not all(math.isfinite(v) for v in (lower, upper)) or not 0 < lower < upper:
                return False
            quoted = price if offset == -1 else float(row["close"])
            if sign * (quoted - float(row[rail])) <= 0:
                return False
        return True
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return False


def confirmed_outer_breakout_ready(frame, price, side, allow_three_short=False):
    """Require a closed breakout/confirmation, with the three-red short option."""
    if allow_three_short and side == "SHORT" and three_closed_short_breakout_ready(frame, price):
        return True
    if not two_closed_bodies_ready(frame, side):
        return False
    try:
        sign = 1 if side == "LONG" else -1
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        breakout, confirmation, live = (frame.iloc[i] for i in (-3, -2, -1))
        for row in (breakout, confirmation, live):
            lower, upper = float(row["kc_lower"]), float(row["kc_upper"])
            if not all(math.isfinite(v) for v in (lower, upper)) or not 0 < lower < upper:
                return False
        price = float(price)
        return (math.isfinite(price) and price > 0
                and sign * (float(breakout["open"]) - float(breakout[rail])) <= 0
                and sign * (float(breakout["close"]) - float(breakout[rail])) > 0
                and sign * (float(confirmation["close"]) - float(confirmation[rail])) > 0
                and sign * (price - float(live[rail])) > 0)
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return False


def outside_entry(frame, price):
    wait = {"action": "WAIT", "side": None, "reason": "KC_INSIDE_CHANNEL"}
    try:
        row = frame.iloc[-1]
        lower, upper, price = float(row['kc_lower']), float(row['kc_upper']), float(price)
        if not all(math.isfinite(v) and v > 0 for v in (lower, upper, price)) or lower >= upper:
            return {**wait, "reason": "KC_DATA_INVALID"}
        side = 'LONG' if price > upper else 'SHORT' if price < lower else None
        if side:
            return {**wait, "reason": "KC_OUTSIDE_WAIT_NEXT_CANDLE"}

    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {**wait, "reason": "KC_DATA_UNAVAILABLE"}
    return wait


def continuation_entry(frame, price):
    """Enter after closed breakout confirmation; shorts may bridge one small red."""
    wait = {"action": "WAIT", "side": None, "reason": "KC_CONTINUATION_WAIT"}
    required = {"open", "high", "low", "close", "ma3", "ma15", "kc_upper", "kc_lower"}
    if frame is None or len(frame) < 5 or not required.issubset(frame.columns):
        return {**wait, "reason": "KC_CONTINUATION_DATA_UNAVAILABLE"}
    try:
        breakout, confirmation, live = frame.iloc[-3], frame.iloc[-2], frame.iloc[-1]
        values = [float(row[key]) for row in (breakout, confirmation, live) for key in required]
        values.append(float(price))
        if not all(math.isfinite(value) and value > 0 for value in values):
            return {**wait, "reason": "KC_CONTINUATION_DATA_INVALID"}
        body = abs(float(confirmation["close"]) - float(confirmation["open"]))
        candle_range = float(confirmation["high"]) - float(confirmation["low"])
        if candle_range <= 0 or body / candle_range < 0.20:
            return wait
        ma3 = [float(row["ma3"]) for row in (breakout, confirmation, live)]
        if not all(math.isfinite(value) and value > 0 for value in ma3):
            return {**wait, "reason": "KC_CONTINUATION_DATA_INVALID"}
        long_signal = (
            float(breakout["open"]) <= float(breakout["kc_upper"]) < float(breakout["close"])
            and float(confirmation["close"]) > float(confirmation["open"])
            and float(confirmation["close"]) > float(confirmation["kc_upper"])
            and float(price) > float(live["kc_upper"])
            and float(confirmation["kc_upper"]) >= float(breakout["kc_upper"])
            and ma3[0] < ma3[1] < ma3[2]
        )
        short_signal = (
            (float(breakout["open"]) >= float(breakout["kc_lower"]) > float(breakout["close"])
             or three_closed_short_breakout_ready(frame, price))
            and float(confirmation["close"]) < float(confirmation["open"])
            and float(confirmation["close"]) < float(confirmation["kc_lower"])
            and float(price) < float(live["kc_lower"])
            and float(confirmation["kc_lower"]) <= float(breakout["kc_lower"])
            and ma3[0] > ma3[1] > ma3[2]
        )
        if long_signal and aligned_direction(frame, "LONG") and confirmed_outer_breakout_ready(frame, price, "LONG"):
            return {"action": "ENTER", "side": "LONG", "reason": "KC_CONTINUATION_LONG"}
        if short_signal and aligned_direction(frame, "SHORT") and confirmed_outer_breakout_ready(frame, price, "SHORT"):
            return {"action": "ENTER", "side": "SHORT", "reason": "KC_CONTINUATION_SHORT"}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {**wait, "reason": "KC_CONTINUATION_DATA_INVALID"}
    return wait


def outside_reentry(frame, price, side):
    """Use the same confirmed CK trend for normal reentries."""
    decision = aligned_entry(frame, price)
    if side not in ("LONG", "SHORT") or decision.get("side") != side:
        return {"action": "WAIT", "side": None, "reason": "KC_REENTRY_WAIT"}
    return decision


def abnormal_pullback_ready(ticket, frame, price):
    """After a close, observe a later candle inside CK before a fresh reclaim."""
    try:
        row = frame.iloc[-1]
        bar = float(row.get("timestamp", row.name))
        exited = float(ticket["exit_bar_id"])
        upper, lower = float(row["kc_upper"]), float(row["kc_lower"])
        if (not all(math.isfinite(v) for v in (bar, exited, upper, lower, price))
                or not 0 < lower < upper or price <= 0 or bar <= exited):
            return False
        if lower <= price <= upper:
            ticket["pullback_bar"] = bar
            return False
        pulled = float(ticket.get("pullback_bar", float("nan")))
        return math.isfinite(pulled) and exited < pulled <= bar
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return False


class OuterChannelEntryStrategy(IEntryStrategy):
    """OOP Strategy class implementing IEntryStrategy for outer channel entry evaluation."""

    def evaluate_entry(
        self,
        frame: pd.DataFrame,
        price: float,
        side: str,
        **kwargs: Any
    ) -> Tuple[bool, str, Dict[str, Any]]:
        decision = aligned_entry(frame, price)
        if decision.get("action") == "ENTER" and decision.get("side") == side:
            return True, decision.get("reason", "OK"), decision
        return False, decision.get("reason", "WAIT"), decision
