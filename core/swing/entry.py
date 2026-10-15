from __future__ import annotations

from typing import Optional, Sequence

from . import gatekeeper as gk
from .gatekeeper import YaoBiEntryGatekeeper
from .models import LONG, SHORT, Bar, EntrySignal, SwingState

COOLDOWN_BARS = gk.COOLDOWN_BARS
WHIPSAW_WINDOW = gk.CHOP_WINDOW
WHIPSAW_MAX_CROSSES = 2
OUTER_HIGH = gk.OUTER_HIGH
OUTER_LOW = gk.OUTER_LOW
MA_LONG_POS = (0.40, 0.70)
MA_SHORT_POS = (0.30, 0.60)
MIN_ROOM_ATR = 1.5   # room to the target band before a MA cross may enter
BREAKOUT_BODY_ATR = 1.5
BREAKOUT_VOL_MULT = 2.0
BREAKOUT_VOL_LOOKBACK = 10
BREAKOUT_MAX_WICK_BODY = 0.25   # rejection wick vs body
TRAP_FLAT_BARS = 5


def midline_crosses(bars: Sequence[Bar], window: int = WHIPSAW_WINDOW) -> int:
    return gk.midline_crosses(bars, window)


def is_whipsaw(bars: Sequence[Bar]) -> bool:
    return midline_crosses(bars) >= WHIPSAW_MAX_CROSSES


def outer_band_blocked(side: str, bar: Bar) -> bool:
    pos = bar.kc_pos
    if pos is None:
        return True
    if side == LONG:
        return pos > OUTER_HIGH or bar.close >= bar.kc_upper
    return pos < OUTER_LOW or bar.close <= bar.kc_lower


def in_cooldown(side: str, state: Optional[SwingState]) -> bool:
    """After any close, MA-cross signals (either side) wait COOLDOWN_BARS bars."""
    return gk.in_cooldown(state)


def _first_break_after_flat(bars: Sequence[Bar], side: str) -> bool:
    """True when the 5 bars before the signal bar were all five-line-flat and the previous bar
    was not already outside the band on the same side (i.e. this is the first single-bar break)."""
    prior = bars[:-1]
    if len(prior) < TRAP_FLAT_BARS + 1:
        return False
    if not all(gk.is_lines_flat(prior[:k]) for k in range(len(prior) - TRAP_FLAT_BARS + 1, len(prior) + 1)):
        return False
    prev = prior[-1]
    outside = prev.close > prev.kc_upper if side == LONG else prev.close < prev.kc_lower
    return not outside


def _breakout(bars: Sequence[Bar], state: Optional[SwingState] = None) -> Optional[EntrySignal]:
    last = bars[-1]
    prior = bars[-1 - BREAKOUT_VOL_LOOKBACK:-1]
    if len(prior) < BREAKOUT_VOL_LOOKBACK or last.atr <= 0:
        return None
    avg_vol = sum(b.volume for b in prior) / len(prior)
    if avg_vol <= 0 or last.volume < BREAKOUT_VOL_MULT * avg_vol:
        return None
    side = None
    if last.close > last.open and last.close > last.kc_upper and (last.close - last.open) >= BREAKOUT_BODY_ATR * last.atr:
        side = LONG
    elif last.close < last.open and last.close < last.kc_lower and (last.open - last.close) >= BREAKOUT_BODY_ATR * last.atr:
        side = SHORT
    if side is None:
        return None
    body = abs(last.close - last.open)
    wick = (last.high - last.close) if side == LONG else (last.close - last.low)
    if wick > BREAKOUT_MAX_WICK_BODY * body:
        return None  # needle / rejection wick: false breakout
    if _first_break_after_flat(bars, side):
        return None  # anti-trap: wait for a 2nd bar to hold outside the band
    if not YaoBiEntryGatekeeper.can_enter(bars, side, state, breakout=True)[0]:
        return None
    return EntrySignal(side, "BREAKOUT_MOMENTUM", "NEXT_OPEN")


def _ma_swing(bars: Sequence[Bar], state: Optional[SwingState]) -> Optional[EntrySignal]:
    prev, last = bars[-2], bars[-1]
    pos = last.kc_pos
    if pos is None:
        return None
    slope = last.ma15 - prev.ma15
    side = None
    if prev.ma5 <= prev.ma15 and last.ma5 > last.ma15 and slope > 0 and MA_LONG_POS[0] <= pos <= MA_LONG_POS[1]:
        side = LONG
    elif prev.ma5 >= prev.ma15 and last.ma5 < last.ma15 and slope < 0 and MA_SHORT_POS[0] <= pos <= MA_SHORT_POS[1]:
        side = SHORT
    if side is None:
        return None
    room = (last.kc_upper - last.close) if side == LONG else (last.close - last.kc_lower)
    if room < MIN_ROOM_ATR * last.atr:
        return None
    if not YaoBiEntryGatekeeper.can_enter(bars, side, state)[0]:
        return None
    return EntrySignal(side, "MA_SWING", "NEXT_OPEN")


def evaluate_entry(bars: Sequence[Bar], state: Optional[SwingState] = None) -> Optional[EntrySignal]:
    """Only legit entries: volume breakout, or gated MA5/MA15 cross. Otherwise None.

    Breakout is exempt from the cross/whipsaw/cooldown/outer-band gates
    (a close beyond the band is the setup itself). Dump-flush flips are in reversal.py.
    """
    if len(bars) < 2 or not bars[-1].is_closed:
        return None
    return _breakout(bars, state) or _ma_swing(bars, state)
