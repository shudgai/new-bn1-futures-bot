"""Adverse live pressure before a position has current net profit protection."""
import math
REASON='EXIT_NO_PROFIT_ADVERSE_PRESSURE'


def no_profit_pressure_ready(snapshot,price,sign,net):
    try:
        if not isinstance(snapshot,dict) or snapshot.get('reason') or snapshot.get('fallback_used') or net>0:
            return False
        stamp=float(snapshot['quote_ms']);bar=math.floor(stamp/60000)*60000
        if snapshot.get('live_bar_ms')!=bar or snapshot.get('closed_bar_ms')!=bar-60000:
            return False
        opened,high,low,ma5,previous,atr=[float(snapshot[k]) for k in ('live_open','live_high','live_low','ma5','last_ma5','atr')]
        price=float(price)
        if not all(math.isfinite(v) and v>0 for v in (opened,high,low,ma5,previous,atr,price)):
            return False
        lower,upper=float(snapshot['kc_lower']),float(snapshot['kc_upper'])
        if not all(math.isfinite(v) and v>0 for v in (lower,upper)) or lower>=upper:
            return False
        edge=upper if sign==1 else lower
        if sign*(price-edge)>0:
            return False
        high,low=max(high,price),min(low,price)
        body=sign*(opened-price)
        return (high>low and body>=.2*atr and body/(high-low)>.25
                and sign*(price-ma5)<0 and sign*(ma5-previous)<=-.05*atr)
    except (KeyError,TypeError,ValueError,OverflowError):
        return False
