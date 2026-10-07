"""Live expansion after two alternating small candles already outside KC."""
import math
from core.services.candle_data import closed_entry_candles

PHASE = 'KC_OUTSIDE_SMALL_PAIR_EXPANSION'
CODES = frozenset((PHASE+'_LONG',PHASE+'_SHORT'))
EVIDENCE_KEYS = ('outer_pattern_anchor_rail','outer_pattern_small_candle_ratios','outer_pattern_body_atr','outer_pattern_small_candle_count')
MIN_BODY_ATR = 1.0
MAX_BODY_ATR = 2.
MAX_SMALL_CHANNEL_RATIO = .5


def evaluate_outer_small_pair(frame, quote, code=None):
    try:
        if code is not None and code not in CODES:
            return None
        closed=closed_entry_candles(frame)
        if len(closed)<2 or len(frame)!=len(closed)+1:
            return None
        first,second=closed.iloc[-2],closed.iloc[-1]
        live=frame.iloc[-1];quote=float(quote)
        for side,sign,rail in [('LONG',1,'kc_upper'),('SHORT',-1,'kc_lower')]:
            signal=PHASE+'_'+side
            if code not in (None,signal):
                continue
            first_body=sign*(float(first.close)-float(first.open))
            second_body=sign*(float(second.close)-float(second.open))
            if not first_body>0 or not second_body<0:
                continue
            prefix=(first,second)
            ratios=[]
            for row in prefix:
                o,h,l,c,upper,lower=[float(row[k]) for k in ('open','high','low','close','kc_upper','kc_lower')]
                if (not all(math.isfinite(v) and v>0 for v in (o,h,l,c,upper,lower))
                        or not l<=min(o,c)<=max(o,c)<=h or h<=l or upper<=lower
                        or sign*(c-float(row[rail]))<=0):
                    break
                ratio=(h-l)/(upper-lower)
                if ratio>MAX_SMALL_CHANNEL_RATIO and not math.isclose(ratio,MAX_SMALL_CHANNEL_RATIO,rel_tol=1e-10):
                    break
                ratios.append(ratio)
            if len(ratios)!=len(prefix):
                continue
            opened,edge,anchor,atr=[float(v) for v in (live.open,live[rail],second[rail],second.atr)]
            if not all(math.isfinite(v) and v>0 for v in (opened,quote,edge,anchor,atr)):
                continue
            # Use the previous closed rail for the opening location. The forming
            # rail moves with price and cannot reconstruct the original context.
            if sign*(opened-anchor)<=0 or sign*(quote-edge)<=0:
                continue
            body=sign*(quote-opened)
            if body<MIN_BODY_ATR*atr or body>MAX_BODY_ATR*atr:
                continue
            if body < 1.5*max(abs(float(row.close)-float(row.open)) for row in prefix):
                continue
            stamps=[float(row.timestamp) for row in (first,second,live)]
            if not all(math.isfinite(v) and v>0 for v in stamps) or any(b-a!=60000 for a,b in zip(stamps,stamps[1:])):
                return None
            return dict(side=side,code=signal,phase=PHASE,outer_pattern_anchor_rail=anchor,
                        outer_pattern_small_candle_ratios=ratios,outer_pattern_body_atr=body/atr,
                        outer_pattern_small_candle_count=len(prefix))
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        return None
    return None
