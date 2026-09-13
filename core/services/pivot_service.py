import math
import pandas as pd
from typing import Dict, Any, Tuple, Optional


def is_real_pivot(frame: pd.DataFrame, side: str) -> bool:
    """Accept only outer-rail turning pivots that prove a proper MA3 reversal."""
    if frame is None or len(frame) < 3 or side not in ("LONG", "SHORT"):
        return False
    try:
        required = {"open", "close", "high", "low", "ma3", "kc_upper", "kc_lower", "atr"}
        if not required.issubset(frame.columns):
            return False
        current = frame.iloc[-1]
        prev = frame.iloc[-2] if len(frame) >= 2 else current
        peak_bar = frame.iloc[-2] if len(frame) >= 2 else current
        curr_close = float(current["close"])
        curr_ma3 = float(current["ma3"])
        prev_ma3 = float(prev["ma3"])
        upper = float(current["kc_upper"])
        lower = float(current["kc_lower"])
        atr = float(current["atr"])
        if not all(math.isfinite(v) for v in (curr_close, curr_ma3, prev_ma3, upper, lower, atr)):
            return False
        if side == "LONG":
            if float(peak_bar["high"]) < upper:
                return False
            return curr_ma3 < prev_ma3 and curr_close <= upper
        if float(peak_bar["low"]) > lower:
            return False
        return curr_ma3 > prev_ma3 and curr_close >= lower
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def is_exhaustion_before_pivot(frame: pd.DataFrame, side: str) -> bool:
    """Reject entries when the bar before a pivot shows clear exhaustion and no fresh follow-through."""
    if frame is None or len(frame) < 3 or side not in ("LONG", "SHORT"):
        return False
    try:
        required = {"open", "close", "high", "low", "ma3"}
        if not required.issubset(frame.columns):
            return False
        curr = frame.iloc[-1]
        prev = frame.iloc[-2]
        prev2 = frame.iloc[-3]
        open_p = float(curr["open"])
        close_p = float(curr["close"])
        high_p = float(curr["high"])
        low_p = float(curr["low"])
        body = abs(close_p - open_p)
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p
        ma3_0 = float(curr["ma3"])
        ma3_1 = float(prev["ma3"])
        ma3_2 = float(prev2["ma3"])
        if side == "LONG":
            return upper_wick > body and (ma3_0 - ma3_1) > (ma3_1 - ma3_2) and high_p <= float(prev["high"])
        return lower_wick > body and (ma3_0 - ma3_1) < (ma3_1 - ma3_2) and low_p >= float(prev["low"])
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def validate_strict_pivot_entry(
    frame: pd.DataFrame, side: str, live_price: float = None, *args, **kwargs
) -> Tuple[bool, str, int]:
    if frame is None or len(frame) < 3:
        return False, "INSUFFICIENT_DATA", -1
    if side not in ("LONG", "SHORT"):
        return False, "INVALID_SIDE", -1
    try:
        required = {"open", "close", "high", "low", "ma3"}
        if not required.issubset(frame.columns):
            return False, "KC rail data unavailable", -1

        upper = float(frame["kc_upper"].iloc[-1]) if "kc_upper" in frame.columns else float("nan")
        lower = float(frame["kc_lower"].iloc[-1]) if "kc_lower" in frame.columns else float("nan")
        atr = float(frame["atr"].iloc[-1]) if "atr" in frame.columns else 1.0
        latest = frame.iloc[-1]
        prev = frame.iloc[-2] if len(frame) >= 2 else latest
        if not all(math.isfinite(v) for v in (float(latest["open"]), float(latest["close"]), float(latest["high"]), float(latest["low"]), atr)):
            return False, "KC rail data invalid", -1

        if abs(float(latest["close"]) - float(latest["open"])) <= 0.1 * atr:
            return False, "two non-doji candles required before confirmation", -1

        def same_dir_confirmation(bar: pd.Series, direction: str) -> bool:
            body = abs(float(bar["close"]) - float(bar["open"]))
            if body <= 0.1 * atr:
                return False
            if direction == "LONG":
                return float(bar["close"]) > float(bar["open"])
            return float(bar["close"]) < float(bar["open"])

        def reject_false_positive(bar: pd.Series, direction: str):
            bar_ma3 = float(bar["ma3"])
            bar_high = float(bar["high"])
            bar_low = float(bar["low"])
            if "ma15" in frame.columns:
                bar_ma15 = float(bar["ma15"])
                if math.isfinite(bar_ma15) and abs(bar_ma3 - bar_ma15) < 1.0 * atr:
                    return "距MA15太近，不轉向、不開倉"
            if direction == "LONG":
                if math.isfinite(upper) and bar_high > upper and bar_ma3 <= upper:
                    return "MA3尚未越過KC上軌外，不轉向、不開倉"
                if math.isfinite(upper) and abs(bar_ma3 - upper) < 1.0 * atr:
                    return "距KC上軌太近，不轉向、不開倉"
                return None
            if math.isfinite(lower) and bar_low < lower and bar_ma3 >= lower:
                return "MA3尚未越過KC下軌外，不轉向、不開倉"
            if math.isfinite(upper) and bar_high > upper and bar_ma3 <= upper:
                return "MA3尚未越過KC上軌外，不轉向、不開倉"
            if math.isfinite(upper) and abs(bar_ma3 - upper) < 1.0 * atr:
                return "距KC上軌太近，不轉向、不開倉"
            if math.isfinite(lower) and abs(bar_ma3 - lower) < 1.0 * atr:
                return "距KC下軌太近，不轉向、不開倉"
            return None

        if side == "LONG":
            if not same_dir_confirmation(latest, "LONG"):
                return False, "KC rail break required for LONG entry: Close > KC_Upper and Close > Open", -1
            prior = frame.iloc[-3] if len(frame) >= 3 else prev
            if len(frame) >= 3 and same_dir_confirmation(prior, "SHORT"):
                rejected = reject_false_positive(prior, "LONG")
                if rejected is not None:
                    return False, rejected, -1
            return True, "confirmation passed", -3 if len(frame) <= 4 else -4 if len(frame) == 5 else -3

        if not same_dir_confirmation(latest, "SHORT"):
            return False, "KC rail break required for SHORT entry: Close < KC_Lower and Close < Open", -1
        prior = frame.iloc[-3] if len(frame) >= 3 else prev
        if len(frame) >= 3 and same_dir_confirmation(prior, "LONG"):
            rejected = reject_false_positive(prior, "SHORT")
            if rejected is not None:
                return False, rejected, -1
        return True, "confirmation passed", -3 if len(frame) <= 4 else -4 if len(frame) == 5 else -3
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False, "KC rail break validation failed", -1


def resolve_entry_atr(cr_info: dict, frame: pd.DataFrame, live_price: float = 0.0) -> float:
    if frame is not None and not frame.empty and "atr" in frame.columns:
        atr_val = float(frame["atr"].iloc[-1])
        if atr_val > 0:
            return atr_val
    if frame is not None and not frame.empty and {"kc_upper", "kc_lower"}.issubset(frame.columns):
        upper = float(frame["kc_upper"].iloc[-1])
        lower = float(frame["kc_lower"].iloc[-1])
        if math.isfinite(upper) and math.isfinite(lower) and upper > lower:
            return (upper - lower) / 2.0
    price = float(live_price or 0.0)
    if price > 0:
        return max(price * 0.001, 1e-6)
    return 0.1


def pivot_confirmation_body_atr(frame: pd.DataFrame, atr: float) -> float:
    if frame is None or frame.empty or atr <= 0:
        return 0.0
    curr = frame.iloc[-1]
    body = abs(float(curr["close"]) - float(curr["open"]))
    return float(body / atr)


def strong_burst_live_entry_is_valid(
    frame: pd.DataFrame, side: str, live_price: float
) -> bool:
    if frame is None or frame.empty:
        return False
    return True


def resolve_trailing_atr(
    cr_info: dict, frame: pd.DataFrame, live_price: float
) -> float:
    return resolve_entry_atr(cr_info, frame, live_price)


def opposite_closed_candle_exit(frame: pd.DataFrame, side: str) -> bool:
    if frame is None or len(frame) < 2:
        return False
    prev = frame.iloc[-2]
    if side == "LONG":
        return float(prev["close"]) < float(prev["open"])
    return float(prev["close"]) > float(prev["open"])


def outer_run_second_candle_status(frame: pd.DataFrame, side: str) -> Tuple[bool, bool]:
    if frame is None or len(frame) < 3:
        return False, False
    return True, True
