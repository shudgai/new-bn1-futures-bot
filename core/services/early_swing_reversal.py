"""Confirmed local reversal near an outer rail, without future candle inference."""
import math
from core.services.candle_data import closed_entry_candles
PHASE='KC_EARLY_SWING_REVERSAL'
CODES={PHASE+'_LONG',PHASE+'_SHORT'}
EXIT='EXIT_EARLY_SWING_REVERSAL'
EVIDENCE_KEYS=('reversal_pivot_ms','reversal_confirmed_ms','reversal_level','reversal_edge')


def evaluate_early_swing(frame,quote,code=None):
    try:
        closed=closed_entry_candles(frame)
        if len(closed)<5 or len(frame)!=len(closed)+1:return None
        live=frame.iloc[-1];q=float(quote);atr=float(closed.iloc[-1].atr)
        rows=closed.iloc[-3:];a,p,c=[r for _,r in rows.iterrows()]
        stamps=[float(r.timestamp) for r in (a,p,c,live)]
        if not all(math.isfinite(v) and v>0 for v in (q,atr,*stamps)):return None
        if any(y-x!=60000 for x,y in zip(stamps,stamps[1:])):return None
        for r in (a,p,c,live):
            o,h,l,cl=map(float,(r.open,r.high,r.low,r.close))
            if not all(math.isfinite(v) and v>0 for v in (o,h,l,cl)) or not l<=min(o,cl)<=max(o,cl)<=h:return None
        for side,sign in (('LONG',1),('SHORT',-1)):
            signal=PHASE+'_'+side
            if code not in (None,signal):continue
            key='low' if sign==1 else 'high';level=float(p[key]);edge=float(c.high if sign==1 else c.low)
            rail=float(p.kc_lower if sign==1 else p.kc_upper)
            if not math.isfinite(rail) or rail<=0:continue
            if any(sign*(float(r[key])-level)<=0 for r in (a,c)):continue
            # Require a real preceding run, a rail-area extreme and a directional
            # completed confirmation; the quote breaks that confirmation now.
            run=sign*(float(a.high if sign==1 else a.low)-level)
            if run<atr or sign*(level-rail)>=0:continue
            if sign*(float(c.close)-float(c.open))<=0:continue
            body = sign*(q-float(live.open))
            if body <= 0 or (body < .5*atr and not math.isclose(body,.5*atr,rel_tol=1e-10)):continue
            if not 0<sign*(q-edge)<=.5*atr:continue
            if sign*(float(live[key])-level)<=0:continue
            return dict(side=side,code=signal,phase=PHASE,reversal_pivot_ms=float(p.timestamp),
                        reversal_confirmed_ms=float(c.timestamp),reversal_level=level,reversal_edge=edge)
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):return None
    return None
