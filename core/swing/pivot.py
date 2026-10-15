from __future__ import annotations

from typing import Optional, Sequence

from .models import LONG, SHORT, Bar

WICK_ATR_MULT = 1.5       # exhaustion wick must span >= 1.5 ATR
WICK_BODY_MULT = 2.0      # ...and >= 2x the candle body
EXHAUST_VOL_MULT = 2.0    # ...on >= 2x the prior-10 average volume
DUMP_BODY_ATR = 1.8
VOL_LOOKBACK = 10


def _avg_volume(prior: Sequence[Bar]) -> float:
    window = prior[-VOL_LOOKBACK:]
    return sum(b.volume for b in window) / len(window) if window else 0.0


def true_reversal(side: str, live: Bar, prior: Sequence[Bar]) -> Optional[str]:
    """Genuine peak/trough against the position on the (still forming) entry bar.

    LONG: volume-exhaustion upper wick at the ceiling, or a >1.8 ATR bearish body
    through the midline. SHORT mirrors. ATR is the last closed bar's (stable scale).
    """
    if not prior:
        return None
    atr = prior[-1].atr
    if atr <= 0:
        return None
    avg_vol = _avg_volume(prior)
    body = abs(live.close - live.open)
    heavy = avg_vol > 0 and live.volume >= EXHAUST_VOL_MULT * avg_vol

    if side == LONG:
        wick = live.high - max(live.open, live.close)
        if live.high >= live.kc_upper and heavy and wick >= WICK_ATR_MULT * atr and wick >= WICK_BODY_MULT * body:
            return "TRUE_PEAK_EXHAUSTION"
        if live.close < live.open and (live.open - live.close) > DUMP_BODY_ATR * atr and live.close < live.ma15:
            return "TRUE_PEAK_DUMP_ENGULF"
    elif side == SHORT:
        wick = min(live.open, live.close) - live.low
        if live.low <= live.kc_lower and heavy and wick >= WICK_ATR_MULT * atr and wick >= WICK_BODY_MULT * body:
            return "TRUE_TROUGH_EXHAUSTION"
        if live.close > live.open and (live.close - live.open) > DUMP_BODY_ATR * atr and live.close > live.ma15:
            return "TRUE_TROUGH_SQUEEZE_ENGULF"
    return None
