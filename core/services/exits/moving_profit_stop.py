"""Monotonic net-positive protection following confirmed post-entry swing pivots."""
import math
from core.services.exits.peak_trailing_exit import estimated_net_pnl
from core.services.candle_data import closed_entry_candles

REASON = 'EXIT_MOVING_PROFIT_STOP'
POLICY = 'confirmed_swing_pivots_v1'
NET_PEAK_ARM_USDT = 1.5


def confirmed_profit_pivots(frame):
    """One closed neighbour on each side confirms a pivot, without live wicks."""
    try:
        rows = closed_entry_candles(frame).iloc[-60:]
        if len(rows) < 3:
            return []
        values = [[float(row[k]) for k in ('timestamp','open','high','low','close')]
                  for _,row in rows.iterrows()]
        if not all(math.isfinite(v) and v > 0 for row in values for v in row):
            return []
        if any(b[0]-a[0] != 60000 for a,b in zip(values,values[1:])):
            return []
        if any(not l <= min(o,c) <= max(o,c) <= h for _,o,h,l,c in values):
            return []
        result=[]
        for i in range(1,len(values)-1):
            stamp,_,high,low,_=values[i]
            for sign,index,value in ((1,3,low),(-1,2,high)):
                if sign*(values[i-1][index]-value)>0 and sign*(values[i+1][index]-value)>0:
                    result.append(dict(sign=sign,price=value,pivot_ms=stamp,confirmed_ms=values[i+1][0]))
        return result
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        return []


def update_profit_stop(state, entry, qty, sign, price, fee, slippage, snapshot=None, protect_peak=False):
    if protect_peak and state.get('profit_stop_source') == 'NET_PEAK_80_PERCENT':
        # Retire only the explicitly cancelled percentage floor and its retry.
        for key in ('profit_stop_price','profit_stop_net','profit_stop_source','profit_stop_triggered'):
            state.pop(key,None)
        if state.get('pending') == REASON:
            state.pop('pending',None);state.pop('trigger',None)
    previous = state.get('profit_stop_price')
    if previous is not None and sign*(price-float(previous)) <= 0:
        state['profit_stop_triggered'] = True
    state['profit_stop_policy'] = POLICY
    if previous is not None and not state.get('profit_stop_source'):
        state['profit_stop_source'] = 'LEGACY_PRESERVED'
    # Preserve the activation threshold for new structural profit protection.
    # The new net-profit floor below is independent of this structural gate.
    can_create_line = not protect_peak
    if protect_peak:
        try:
            peak = float(state.get('peak_price', entry))
            scale = float(state.get('atr') or 0.)
            peak_net = estimated_net_pnl(entry, peak, qty, sign, fee, slippage)
            if (all(math.isfinite(v) for v in (peak,scale,peak_net)) and scale > 0
                    and sign*(peak-entry) >= 1.5*scale and peak_net >= .5):
                state['peak_profit_floor_armed'] = True
            can_create_line = bool(state.get('peak_profit_floor_armed'))
        except (TypeError, ValueError, OverflowError):
            pass
    # New lines require synchronized closed evidence. Existing protection and
    # triggered retries remain effective even when market data is unavailable.
    try:
        if can_create_line and isinstance(snapshot,dict) and not snapshot.get('reason') and not snapshot.get('fallback_used'):
            bar = math.floor(float(snapshot['quote_ms'])/60000)*60000
            if snapshot.get('closed_bar_ms') == bar-60000:
                opened_ms = float(state['identity'][1])*1000
                for pivot in snapshot.get('profit_pivot_candidates',[]):
                    candidate = float(pivot['price'])
                    pivot_ms,confirmed_ms = float(pivot['pivot_ms']),float(pivot['confirmed_ms'])
                    if (pivot.get('sign') != sign or pivot_ms < opened_ms
                            or not all(math.isfinite(v) and v>0 for v in (candidate,pivot_ms,confirmed_ms))
                            or confirmed_ms != pivot_ms+60000 or confirmed_ms > bar-60000):
                        continue
                    if protect_peak:
                        scale=float(state.get('atr') or 0.)
                        if not math.isfinite(scale) or scale<=0:
                            continue
                        candidate -= sign*.1*scale
                    net = estimated_net_pnl(entry,candidate,qty,sign,fee,slippage)
                    line = state.get('profit_stop_price')
                    if net>0 and (line is None or sign*(candidate-float(line))>0):
                        state.update(profit_stop_price=candidate,profit_stop_net=net,
                                     profit_stop_source='CONFIRMED_SWING',profit_stop_pivot_ms=pivot_ms,
                                     profit_stop_confirmed_ms=confirmed_ms)
    except (KeyError,TypeError,ValueError,OverflowError):
        pass
    # Net-profit protection does not wait for ATR or a confirmed swing pivot.
    if protect_peak:
        try:
            peak=float(state.get('peak_price',entry))
            observed=estimated_net_pnl(entry,peak,qty,sign,fee,slippage)
            peak_net=max(observed,float(state.get('peak_net_pnl',observed)))
            if all(math.isfinite(v) for v in (peak,peak_net,entry,qty,fee,slippage)) and qty>0:
                if peak_net>=NET_PEAK_ARM_USDT:
                    state['net_peak_floor_armed']=True
                if state.get('net_peak_floor_armed') and peak_net>0:
                    target=.7*peak_net
                    candidate=(target/qty+(sign+fee)*entry)/((sign-fee)*(1-sign*slippage))
                    line=state.get('profit_stop_price')
                    if math.isfinite(candidate) and candidate>0 and (line is None or sign*(candidate-float(line))>0):
                        state.update(profit_stop_price=candidate,profit_stop_net=target,
                                     profit_stop_source='NET_PEAK_70_PERCENT')
        except (TypeError,ValueError,OverflowError,ZeroDivisionError):
            pass
    line = state.get('profit_stop_price')
    if line is not None and sign*(price-float(line)) <= 0:
        state['profit_stop_triggered'] = True
    if state.get('profit_stop_triggered'):
        state.update(pending=REASON,trigger=REASON)
        return dict(action='FULL_CLOSE',type=REASON,reason=REASON,trigger=REASON,price=price)
    return None
