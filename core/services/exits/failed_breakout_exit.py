"""Exit a newly opened breakout only after its recorded level is invalidated."""
import math
REASON='EXIT_FAILED_BREAKOUT_RECLAIM'
WINDOW_MS=180000
BUFFER_ATR=.1


def failed_breakout_ready(position,state,price,sign,atr):
    try:
        level=float(position.get('entry_failure_level') or 0.)
        opened=float(position['open_timestamp'])*1000
        stamp=float(state.get('last_ms') or 0.)
        if not all(math.isfinite(v) and v>0 for v in (level,opened,stamp,price,atr)):return False
        if not 0<=stamp-opened<=WINDOW_MS:return False
        return sign*(price-level)<-BUFFER_ATR*atr
    except (KeyError,TypeError,ValueError,OverflowError):return False
