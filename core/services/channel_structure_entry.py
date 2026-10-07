"""Closed-pivot validated early short entry inside the channel."""
import math
from core.services.candle_data import closed_entry_candles
PHASE='KC_CHANNEL_STRUCTURE_BREAK'
CODES={PHASE+'_SHORT'}
EVIDENCE_KEYS=('structure_entry_level','structure_entry_pivot_ms','structure_entry_confirmed_ms')


def evaluate_channel_structure(frame,quote,code=None):
    try:
        if code not in (None,PHASE+'_SHORT'):return None
        closed=closed_entry_candles(frame)
        if len(closed)<15 or len(frame)!=len(closed)+1:return None
        rows=closed.iloc[-20:]
        values=[[float(r[k]) for k in ('timestamp','open','high','low','close')] for _,r in rows.iterrows()]
        if not all(math.isfinite(v) and v>0 for r in values for v in r):return None
        if any(b[0]-a[0]!=60000 for a,b in zip(values,values[1:])):return None
        if any(not l<=min(o,c)<=max(o,c)<=h for _,o,h,l,c in values):return None
        live=frame.iloc[-1];q=float(quote)
        opened,lower,upper,atr=map(float,(live.open,live.kc_lower,live.kc_upper,closed.iloc[-1].atr))
        if not all(math.isfinite(v) and v>0 for v in (q,opened,lower,upper,atr)):return None
        if not lower<=q<opened<=upper or opened-q<.5*atr and not math.isclose(opened-q,.5*atr,rel_tol=1e-10):return None
        if float(live.timestamp)-values[-1][0]!=60000:return None
        prev,current=map(float,closed.kc_middle.iloc[-2:])
        if not all(math.isfinite(v) and v>0 for v in (prev,current)) or current>=prev:return None
        prices=[float(v) for v in closed.close.iloc[-15:]]
        ma15=(sum(prices[-14:])+q)/15
        ma5=(sum(prices[-4:])+q)/5
        if q>=min(ma5,ma15):return None
        # Use the latest completed pivot only; do not select an older easier level.
        pivots=[(i,r) for i,r in enumerate(values[1:-1],1) if r[3]<values[i-1][3] and r[3]<values[i+1][3]]
        if not pivots:return None
        i,pivot=pivots[-1];level=pivot[3]
        if any(r[3]<=level for r in values[i+1:]) or not q<level<=opened:return None
        return dict(side='SHORT',code=PHASE+'_SHORT',phase=PHASE,structure_entry_level=level,
                    structure_entry_pivot_ms=pivot[0],structure_entry_confirmed_ms=values[i+1][0])
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):return None
