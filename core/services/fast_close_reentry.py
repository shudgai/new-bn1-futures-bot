"""One confirmed strategy close authorizes one fresh same-side continuation."""
import math
import time

from core.services.candle_data import closed_entry_candles

CODES = {"FAST_CLOSE_REENTRY_LONG", "FAST_CLOSE_REENTRY_SHORT"}
CLOSE_REASONS = {
    "Channel Swing EXIT_FIXED_ATR_HALF_STEP_PROFIT",
    "Channel Swing EXIT_KC_REVERSE_CLOSED_MA5_PEAK_010_ATR",
    "Channel Swing EXIT_KC_REVERSE_LIVE_MA5_PEAK_010_ATR",
}
EVIDENCE_KEYS = ("reentry_close_id", "reentry_live_ma5", "reentry_previous_ma5",
                 "reentry_kc_previous", "reentry_kc_current",
                 "reentry_ma15_previous", "reentry_ma15_current")


def live_ma5_values(frame, quote):
    closed = closed_entry_candles(frame)
    if len(closed) < 5 or len(frame) != len(closed)+1:
        raise ValueError("Missing live MA5 data")
    previous = float(closed.iloc[-1].ma5)
    closes = [float(v) for v in closed.close.tail(4)]
    current = (sum(closes)+float(quote))/5.
    if not all(math.isfinite(v) and v > 0 for v in [previous, current, float(quote), *closes]):
        raise ValueError("Invalid live MA5 data")
    return previous, current


def evaluate(account, symbol, frame, quote, code=None):
    try:
        if (account is None or symbol in getattr(account, "positions", {})
                or symbol in getattr(account, "pending_limit_orders", {})
                or symbol in getattr(account, "closing_lock", set())):
            return None
        trades = [t for t in getattr(account, "trades", []) if t.get("symbol") == symbol]
        closes = [t for t in trades if t.get("action") in ("CLOSE_LONG", "CLOSE_SHORT")]
        if not closes:
            return None
        close = max(closes, key=lambda t: float(t["id"]))
        close_id = float(close["id"])
        side = "LONG" if close["action"] == "CLOSE_LONG" else "SHORT"
        signal = "FAST_CLOSE_REENTRY_"+side
        if (code not in (None, signal) or close.get("status") != "CLOSED"
                or close.get("entry_mode") != "CHANNEL_SWING"
                or close.get("reason") not in CLOSE_REASONS
                or not math.isfinite(close_id) or close_id <= 0 or close_id > time.time()*1000
                or any(t.get("action") in ("OPEN_LONG", "OPEN_SHORT")
                       and float(t["id"]) >= close_id for t in trades)):
            return None
        closed = closed_entry_candles(frame)
        if len(closed) < 5 or len(frame) != len(closed)+1:
            return None
        stamp = float(frame.iloc[-1].timestamp)
        if stamp < math.floor(close_id/60000)*60000:
            return None
        if any((t.get("entry_snapshot") or {}).get("entry_phase") == "FAST_CLOSE_REENTRY"
               and math.floor(float(t["id"])/60000)*60000 == stamp
               for t in trades if t.get("action") in ("OPEN_LONG", "OPEN_SHORT")):
            return None
        previous, live_ma5 = live_ma5_values(frame, quote)
        middle = [float(v) for v in closed.kc_middle.tail(2)]
        ma15 = [float(v) for v in closed.ma15.tail(2)]
        atr = float(closed.iloc[-1].atr)
        values = [previous, live_ma5, *middle, *ma15, atr, float(quote)]
        if not all(math.isfinite(v) and v > 0 for v in values):
            return None
        sign = 1 if side == "LONG" else -1
        if any(sign*(b-a) <= max(a, b)*1e-12
               for a, b in ((previous, live_ma5), tuple(middle), tuple(ma15))):
            return None
        prev_stamp = float(closed.iloc[-1].timestamp)
        return dict(action="ENTER", side=side, type=signal, reason=signal,
                    price=float(quote), entry_atr=atr, confirmation_bar_id=stamp,
                    close_price=float(closed.iloc[-1].close), intrabar=True,
                    entry_phase="FAST_CLOSE_REENTRY", breakout_bar_id=stamp,
                    pair_confirmation_bar_id=prev_stamp, third_bar_id=stamp,
                    pending_second_bar_id=prev_stamp, pending_wait_bars=1, pending_max_wait_bars=1,
                    pending_signal_id=f"{symbol}_FAST_REENTRY_{int(close_id)}_{side}",
                    reentry_close_id=close_id, reentry_live_ma5=live_ma5,
                    reentry_previous_ma5=previous, reentry_kc_previous=middle[0],
                    reentry_kc_current=middle[1], reentry_ma15_previous=ma15[0],
                    reentry_ma15_current=ma15[1])
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None
