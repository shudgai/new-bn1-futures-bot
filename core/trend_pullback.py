"""Causal five-minute trend/pullback policy shared by research and execution."""
from dataclasses import dataclass
import math
import pandas as pd

MODE = 'TREND_PULLBACK_5M'
TIMEFRAME = '5m'

@dataclass(frozen=True)
class Rules:
    slope_bars: int = 3
    proximity_atr: float = .25
    stop_buffer_atr: float = .25
    max_stop_atr: float = 2.5
    min_net_rr: float = 1.5
    swing_lookback: int = 60

DEFAULT_RULES = Rules()


def confirmed_swings(closed):
    """Two left and two right closed candles; no future/unclosed pivot use."""
    lows = closed['low'].astype(float).to_numpy()
    highs = closed['high'].astype(float).to_numpy()
    result = []
    for i in range(2, len(closed)-2):
        if lows[i] < min(lows[i-2],lows[i-1],lows[i+1],lows[i+2]):
            result.append((i,'LOW',float(lows[i])))
        if highs[i] > max(highs[i-2],highs[i-1],highs[i+1],highs[i+2]):
            result.append((i,'HIGH',float(highs[i])))
    return result


def evaluate(frame, price, position=None, *, fee_rate=.0005, slippage=.0001, rules=DEFAULT_RULES):
    """Final row is forming. Initial/trailed stops are absolute price levels."""
    held = str((position or {}).get('side') or '').upper()
    wait = dict(action='HOLD' if held else 'WAIT',side=None,reason='PULLBACK_WAIT')
    # An existing stop remains enforceable even if candle fetch/indicators fail.
    stop = float((position or {}).get('sl') or 0.)
    if held and stop > 0 and math.isfinite(price) and price > 0:
        if (held=='LONG' and price<=stop) or (held=='SHORT' and price>=stop):
            return dict(action='EXIT',side=None,reason='PULLBACK_STRUCTURE_STOP',stop_loss=stop)
    required = {'open','high','low','close','ma15','ema_20','atr'}
    if frame is None or len(frame)<8 or not required.issubset(frame.columns):
        return {**wait,'reason':'PULLBACK_DATA_UNAVAILABLE'}
    c = frame.iloc[:-1].tail(rules.swing_lookback+3)
    values = c[list(required)].apply(pd.to_numeric,errors='coerce')
    if not values.iloc[-5:].map(lambda x: math.isfinite(x) and x>0).all().all():
        return {**wait,'reason':'PULLBACK_DATA_INVALID'}
    last, pullback = c.iloc[-1], c.iloc[-2]
    atr = float(last.atr)
    if held:
        if stop <= 0:
            return {**wait,'reason':'PULLBACK_MISSING_STOP'}
        swing_type = 'LOW' if held=='LONG' else 'HIGH'
        entry_ms = float(position.get('open_timestamp') or 0)*1000
        for i, kind, level in confirmed_swings(c):
            if kind != swing_type:
                continue
            if 'timestamp' in c and float(c.iloc[i].timestamp) <= entry_ms:
                continue
            candidate = level + (-1 if held=='LONG' else 1)*rules.stop_buffer_atr*float(c.iloc[i].atr)
            # A newly confirmed pivot can only tighten protection, never loosen it.
            if held=='LONG' and stop<candidate<price:
                stop = candidate
            elif held=='SHORT' and price<candidate<stop:
                stop = candidate
        return {**wait,'reason':'PULLBACK_TRAIL_STRUCTURE','stop_loss':stop}
    old = c.iloc[-1-rules.slope_bars]
    up = last.ma15>old.ma15 and last.ema_20>old.ema_20
    down = last.ma15<old.ma15 and last.ema_20<old.ema_20
    side = 'LONG' if up else 'SHORT' if down else None
    if not side:
        return {**wait,'reason':'PULLBACK_TREND_UNALIGNED'}
    zone_low = min(pullback.ma15,pullback.ema_20)-rules.proximity_atr*pullback.atr
    zone_high = max(pullback.ma15,pullback.ema_20)+rules.proximity_atr*pullback.atr
    if not (pullback.low<=zone_high and pullback.high>=zone_low):
        return {**wait,'reason':'PULLBACK_NO_RETEST'}
    long = side=='LONG'
    if not ((last.close>last.open and last.close>pullback.high) if long
            else (last.close<last.open and last.close<pullback.low)):
        return {**wait,'reason':'PULLBACK_CONFIRMATION_PENDING'}
    prior = c.iloc[:-2]
    swings = [level for _,kind,level in confirmed_swings(prior)
              if kind==('LOW' if long else 'HIGH')]
    if not swings:
        return {**wait,'reason':'PULLBACK_NO_PRIOR_STRUCTURE'}
    extreme = min(pullback.low,last.low) if long else max(pullback.high,last.high)
    if not (extreme>swings[-1] if long else extreme<swings[-1]):
        return {**wait,'reason':'PULLBACK_PRIOR_STRUCTURE_BROKEN'}
    stop = float(extreme + (-1 if long else 1)*rules.stop_buffer_atr*atr)
    risk = price-stop if long else stop-price
    target = float(prior.high.tail(20).max() if long else prior.low.tail(20).min())
    reward = target-price if long else price-target
    cost = price*2*(fee_rate+slippage)
    rr = (reward-cost)/(risk+cost) if risk+cost>0 else -1.
    if not math.isfinite(price) or price<=0 or risk<=0 or risk>rules.max_stop_atr*atr:
        return {**wait,'reason':'PULLBACK_STOP_DISTANCE_INVALID'}
    if rr<rules.min_net_rr:
        return {**wait,'reason':'PULLBACK_NET_RR_TOO_LOW'}
    return dict(action='ENTER',side=side,reason='PULLBACK_CONFIRMED',
                stop_loss=stop,net_rr=rr,atr=atr,reference_target=target)
