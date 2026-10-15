"""Yao-Bi entry gatekeeper: forbidden-entry conditions, fail-closed (any hit => no entry)."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .models import LONG, SHORT, Bar, SwingState

COOLDOWN_BARS = 4              # 3~5 bars after any close
CHOP_WINDOW = 12
MIN_HISTORY = 21               # 20 prior bars for the volume average + the signal bar
OUTER_HIGH, OUTER_LOW = 0.80, 0.20
OVEREXTEND_ATR = 1.5
LOW_VOLUME_RATIO = 0.5         # vs prior-20 average
FLAT_SLOPE_ATR = 0.05          # |per-bar slope| < 5% of ATR counts as flat
SINE_WINDOW = 12
SINE_MIN_FLIPS = 5             # MA5 direction reversals inside the window
SINE_MAX_RANGE_ATR = 1.0       # ...with a tiny total swing
WICK_BODY_MULT = 1.5

# Breakout bars are the setup itself: they sit outside the band, and a squeeze (flat lines)
# precedes them. Only the volume/wick/direction vetoes apply to them.
BREAKOUT_EXEMPT = {"FLAT_LINES", "CHOP", "CHASE", "OVEREXTENDED", "COOLDOWN"}


def _slope(values: Sequence[float]) -> float:
    return values[-1] - values[-2]


def is_lines_flat(bars: Sequence[Bar]) -> bool:
    """All five lines (KC upper/mid/lower, MA5, MA15) are near-horizontal."""
    last, prev = bars[-1], bars[-2]
    thr = FLAT_SLOPE_ATR * last.atr
    slopes = (last.kc_upper - prev.kc_upper, last.ma15 - prev.ma15, last.kc_lower - prev.kc_lower,
              last.ma5 - prev.ma5, last.ma15 - prev.ma15)
    return all(abs(s) < thr for s in slopes)


def is_ma5_flat(bars: Sequence[Bar]) -> bool:
    return abs(bars[-1].ma5 - bars[-2].ma5) < FLAT_SLOPE_ATR * bars[-1].atr


def is_sine_wave(bars: Sequence[Bar]) -> bool:
    """MA5 zig-zags up/down at high frequency with a tiny total amplitude."""
    window = [b.ma5 for b in bars[-SINE_WINDOW:]]
    if len(window) < SINE_WINDOW:
        return False
    signs = [1 if b > a else -1 for a, b in zip(window, window[1:]) if b != a]
    flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    return flips >= SINE_MIN_FLIPS and (max(window) - min(window)) <= SINE_MAX_RANGE_ATR * bars[-1].atr


def midline_crosses(bars: Sequence[Bar], window: int = CHOP_WINDOW) -> int:
    recent = bars[-window:]
    n = 0
    for p, c in zip(recent, recent[1:]):
        a, b = p.close - p.ma15, c.close - c.ma15
        if (a < 0 < b) or (a > 0 > b):
            n += 1
    return n


def ma_crosses(bars: Sequence[Bar], window: int = CHOP_WINDOW) -> int:
    """MA5/MA15 crossings in the window (the signal bar's own cross counts)."""
    recent = bars[-window:]
    n = 0
    for p, c in zip(recent, recent[1:]):
        a, b = p.ma5 - p.ma15, c.ma5 - c.ma15
        if (a <= 0 < b) or (a >= 0 > b):
            n += 1
    return n


def has_indecision_wicks(bars: Sequence[Bar]) -> bool:
    """Two consecutive bars whose total wick length exceeds 1.5x the body."""
    for b in bars[-2:]:
        wicks = (b.high - b.low) - b.body
        if not wicks > WICK_BODY_MULT * b.body:
            return False
    return True


def in_cooldown(state: Optional[SwingState]) -> bool:
    if state is None or state.bars_since_close is None or state.last_close_side is None:
        return False
    return state.bars_since_close < COOLDOWN_BARS


class YaoBiEntryGatekeeper:
    @staticmethod
    def can_enter(
        bars: Sequence[Bar], direction: str, state: Optional[SwingState] = None, breakout: bool = False,
    ) -> Tuple[bool, str]:
        """`bars` are closed bars, last = signal bar. Returns (allowed, reason)."""
        if direction not in (LONG, SHORT):
            return False, "FORBIDDEN_BAD_DIRECTION"
        if len(bars) < MIN_HISTORY or not bars[-1].is_closed:
            return False, "FORBIDDEN_INSUFFICIENT_HISTORY"
        last = bars[-1]
        if last.atr <= 0:
            return False, "FORBIDDEN_BAD_ATR"
        pos = last.kc_pos
        if pos is None:
            return False, "FORBIDDEN_BAD_CHANNEL"

        def skip(tag: str) -> bool:
            return breakout and tag in BREAKOUT_EXEMPT

        ma5_slope = _slope([b.ma5 for b in bars])
        if direction == LONG and ma5_slope <= 0:
            return False, "FORBIDDEN_MA5_SLOPING_DOWN"
        if direction == SHORT and ma5_slope >= 0:
            return False, "FORBIDDEN_MA5_SLOPING_UP"

        if not skip("COOLDOWN") and in_cooldown(state):
            return False, "FORBIDDEN_COOLDOWN"
        if not skip("FLAT_LINES") and (is_lines_flat(bars) or is_ma5_flat(bars)):
            return False, "FORBIDDEN_LINES_FLAT"
        if not skip("CHOP") and (is_sine_wave(bars) or midline_crosses(bars) >= 2 or ma_crosses(bars) >= 2):
            return False, "FORBIDDEN_CHOP_MARKET"
        if not skip("CHASE"):
            if direction == LONG and (pos > OUTER_HIGH or last.close >= last.kc_upper):
                return False, "FORBIDDEN_CHASE_CEILING"
            if direction == SHORT and (pos < OUTER_LOW or last.close <= last.kc_lower):
                return False, "FORBIDDEN_CHASE_FLOOR"
        if not skip("OVEREXTENDED") and abs(last.close - last.ma15) > OVEREXTEND_ATR * last.atr:
            return False, "FORBIDDEN_OVEREXTENDED"

        avg20 = sum(b.volume for b in bars[-21:-1]) / 20.0
        if avg20 <= 0 or last.volume < LOW_VOLUME_RATIO * avg20:
            return False, "FORBIDDEN_LOW_VOLUME"
        if has_indecision_wicks(bars):
            return False, "FORBIDDEN_LONG_WICKS"
        return True, "PASSED_GATEKEEPER"
