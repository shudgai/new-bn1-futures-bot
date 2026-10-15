from __future__ import annotations

from typing import Optional, Sequence

from .models import LONG, SHORT, Bar, EntrySignal, SwingState

COOLDOWN_BARS = 4
WHIPSAW_WINDOW = 10
WHIPSAW_MAX_CROSSES = 2
OUTER_HIGH = 0.80
OUTER_LOW = 0.20
MA_LONG_POS = (0.40, 0.70)
MA_SHORT_POS = (0.30, 0.60)
BREAKOUT_BODY_ATR = 1.2
BREAKOUT_VOL_MULT = 1.3
BREAKOUT_VOL_LOOKBACK = 10


def midline_crosses(bars: Sequence[Bar], window: int = WHIPSAW_WINDOW) -> int:
    """Closes crossing the KC midline within the last `window` bars."""
    recent = bars[-window:]
    crosses = 0
    for prev, cur in zip(recent, recent[1:]):
        a, b = prev.close - prev.ma15, cur.close - cur.ma15
        if (a < 0 < b) or (a > 0 > b):
            crosses += 1
    return crosses


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
    if state is None or state.bars_since_close is None or state.last_close_side is None:
        return False
    return state.bars_since_close < COOLDOWN_BARS


def _breakout(bars: Sequence[Bar]) -> Optional[EntrySignal]:
    last = bars[-1]
    prior = bars[-1 - BREAKOUT_VOL_LOOKBACK:-1]
    if len(prior) < BREAKOUT_VOL_LOOKBACK or last.atr <= 0:
        return None
    avg_vol = sum(b.volume for b in prior) / len(prior)
    if avg_vol <= 0 or last.volume < BREAKOUT_VOL_MULT * avg_vol:
        return None
    if last.close > last.open and last.close > last.kc_upper and (last.close - last.open) >= BREAKOUT_BODY_ATR * last.atr:
        return EntrySignal(LONG, "BREAKOUT_MOMENTUM", "NEXT_OPEN")
    if last.close < last.open and last.close < last.kc_lower and (last.open - last.close) >= BREAKOUT_BODY_ATR * last.atr:
        return EntrySignal(SHORT, "BREAKOUT_MOMENTUM", "NEXT_OPEN")
    return None


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
    if in_cooldown(side, state) or is_whipsaw(bars) or outer_band_blocked(side, last):
        return None
    return EntrySignal(side, "MA_SWING", "NEXT_OPEN")


def evaluate_entry(bars: Sequence[Bar], state: Optional[SwingState] = None) -> Optional[EntrySignal]:
    """Only legit entries: volume breakout, or gated MA5/MA15 cross. Otherwise None.

    Breakout is exempt from the cross/whipsaw/cooldown/outer-band gates
    (a close beyond the band is the setup itself). Dump-flush flips are in reversal.py.
    """
    if len(bars) < 2 or not bars[-1].is_closed:
        return None
    return _breakout(bars) or _ma_swing(bars, state)
