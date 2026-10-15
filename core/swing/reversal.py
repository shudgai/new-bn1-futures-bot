from __future__ import annotations

from typing import Optional, Sequence

from .models import LONG, SHORT, Bar, EntrySignal

BODY_ATR_MULT = 1.8


def _engulfs_prior_three(bars: Sequence[Bar], side: str) -> bool:
    prior = bars[-4:-1]
    if len(prior) < 3:
        return False
    last = bars[-1]
    if side == LONG:  # bearish bar swallowing three bullish bars
        if not all(b.close > b.open for b in prior):
            return False
        return last.open >= max(b.close for b in prior) and last.close <= min(b.open for b in prior)
    if not all(b.close < b.open for b in prior):
        return False
    return last.open <= min(b.close for b in prior) and last.close >= max(b.open for b in prior)


def evaluate_dump_reversal(position_side: str, bars: Sequence[Bar]) -> Optional[EntrySignal]:
    """Flush candle against the position: close it and flip at bar close.

    Returns the NEW (opposite) entry signal; the caller must close first.
    Exempt from midline slope, cooldown and min-hold rules.
    """
    if not bars or not bars[-1].is_closed or bars[-1].atr <= 0:
        return None
    last = bars[-1]
    if position_side == LONG:
        if last.close >= last.open:
            return None
        big = (last.open - last.close) > BODY_ATR_MULT * last.atr
        if (big or _engulfs_prior_three(bars, LONG)) and (last.close < last.ma5 or last.close < last.ma15):
            return EntrySignal(SHORT, "DUMP_FLUSH_REVERSAL", "BAR_CLOSE")
    elif position_side == SHORT:
        if last.close <= last.open:
            return None
        big = (last.close - last.open) > BODY_ATR_MULT * last.atr
        if (big or _engulfs_prior_three(bars, SHORT)) and (last.close > last.ma5 or last.close > last.ma15):
            return EntrySignal(LONG, "DUMP_FLUSH_REVERSAL", "BAR_CLOSE")
    return None
