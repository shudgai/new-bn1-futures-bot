from __future__ import annotations

from typing import Optional, Sequence

from .models import LONG, SHORT, Bar

DUMP_BODY_ATR = 1.8
WICK_BODY_MULT = 2.0
CLIMAX_VOL_MULT = 2.0
VOL_LOOKBACK = 10
EPS = 1e-12


def history_before(bar: Bar, prior: Sequence[Bar]) -> Sequence[Bar]:
    """`prior` may or may not already contain `bar` as its last element; drop it if so."""
    return prior[:-1] if prior and prior[-1] == bar else prior


def true_reversal(side: str, live: Bar, prior: Sequence[Bar]) -> Optional[str]:
    """True peak (long) / true trough (short): the only non-ratchet exits that pass the trend filter.

    1. Climax needle: opposing wick >= 2x body on volume >= 2x the 10-bar average.
    2. Engulfing dump/squeeze: body >= 1.8 ATR that closes through MA5 and threatens the midline.
    ATR is the last closed bar's (stable scale); `live` may still be forming.
    """
    hist = history_before(live, prior)
    if not hist or hist[-1].atr <= 0:
        return None
    atr = hist[-1].atr
    body = abs(live.close - live.open)
    vols = [b.volume for b in hist[-VOL_LOOKBACK:]]
    avg_vol = sum(vols) / len(vols) if vols else 0.0
    climax_vol = avg_vol > 0 and live.volume >= CLIMAX_VOL_MULT * avg_vol
    if side == LONG:
        wick = live.high - max(live.open, live.close)
        if climax_vol and wick > 0 and wick + EPS >= WICK_BODY_MULT * body:
            return "TRUE_PEAK_CLIMAX_NEEDLE"
        if (live.close < live.open and body >= DUMP_BODY_ATR * atr and live.close < live.ma5
                and live.close <= live.ma15 + 0.5 * atr):
            return "EXTREME_DUMP_BREAKS_MIDLINE"
    elif side == SHORT:
        wick = min(live.open, live.close) - live.low
        if climax_vol and wick > 0 and wick + EPS >= WICK_BODY_MULT * body:
            return "TRUE_TROUGH_CLIMAX_NEEDLE"
        if (live.close > live.open and body >= DUMP_BODY_ATR * atr and live.close > live.ma5
                and live.close >= live.ma15 - 0.5 * atr):
            return "EXTREME_SQUEEZE_BREAKS_MIDLINE"
    return None
