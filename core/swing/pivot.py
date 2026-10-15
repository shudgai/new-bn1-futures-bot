from __future__ import annotations

from typing import Optional, Sequence

from .models import LONG, SHORT, Bar

DUMP_BODY_ATR = 1.8


def true_reversal(side: str, live: Bar, prior: Sequence[Bar]) -> Optional[str]:
    """Extreme single-bar body (> 1.8 ATR) against the position that breaks the midline.

    ATR is the last closed bar's (stable scale); `live` may still be forming.
    """
    if not prior or prior[-1].atr <= 0:
        return None
    atr = prior[-1].atr
    if side == LONG and live.close < live.open and (live.open - live.close) > DUMP_BODY_ATR * atr and live.close < live.ma15:
        return "EXTREME_DUMP_BREAKS_MIDLINE"
    if side == SHORT and live.close > live.open and (live.close - live.open) > DUMP_BODY_ATR * atr and live.close > live.ma15:
        return "EXTREME_SQUEEZE_BREAKS_MIDLINE"
    return None
