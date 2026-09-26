"""Monotone chandelier stops with chronological, closed one-minute decisions."""
import math
import time

from core.services.candle_data import closed_entry_candles

POLICY = "chandelier_1m_v2"
STOP_REASON = "EXIT_TRAILING_ATR_1M_CLOSED"
MIDDLE_REASON = "EXIT_KC_MIDDLE_BODY_CLOSED"
STATE_KEYS = ("entry_atr", "atr_sl", "atr_tp", "atr_protection_version",
              "chandelier_state", "sl", "tp", "channel_profit_protection")


def valid(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def initialize_chandelier(position, entry_price, side, atr):
    if side not in ("LONG", "SHORT") or not all(valid(v) for v in (entry_price, atr)):
        raise ValueError("Invalid chandelier inputs")
    entry_price, atr = float(entry_price), float(atr)
    sign = 1 if side == "LONG" else -1
    stop = entry_price - sign * 1.5 * atr
    if not valid(stop):
        raise ValueError("Invalid chandelier stop")
    position.update(entry_atr=atr, atr_sl=stop, atr_tp=0., atr_protection_version=2,
                    sl=stop, tp=0., initial_sl=stop, initial_risk=1.5*atr)
    position["chandelier_state"] = dict(
        policy=POLICY, stop=stop, confirmed_stop=stop, highest_price=entry_price,
        lowest_price=entry_price, confirmed_high=entry_price, confirmed_low=entry_price,
        atr=atr, last_closed_bar=-1, quote_bars={}, pending=False,
    )
    position["channel_profit_protection"] = dict(policy=POLICY, pending=False)


def _tighten(side, *values):
    return (max if side == "LONG" else min)(values)


def _publish(position, state):
    position.update(atr_sl=state['stop'], sl=state['stop'], atr_tp=0., tp=0.,
                    atr_protection_version=2)
    position['channel_profit_protection'] = dict(
        policy=POLICY, pending=state['pending'], reason=state.get('reason'))


def chandelier_exit_reason(position, price, frame=None, *, now_ms=None):
    """Never compare an earlier close with a later quote's tightened stop.

    Quote extrema tighten the displayed stop immediately, but each minute keeps
    its own observations for close confirmation. A partial entry candle uses
    only prices actually observed since entry, never its pre-entry wick.
    """
    if not valid(price):
        return None
    now_ms = float(time.time()*1000 if now_ms is None else now_ms)
    if not valid(now_ms):
        return None
    side, entry = position.get('side'), position.get('entry_price')
    if side not in ('LONG', 'SHORT') or not valid(entry):
        return None
    entry = float(entry)
    sign = 1 if side == 'LONG' else -1
    state = position.get('chandelier_state')
    if not isinstance(state, dict) or state.get('policy') != POLICY:
        atr = position.get('entry_atr')
        if not valid(atr):
            return None
        old_stop = position.get('atr_sl') or position.get('sl')
        initialize_chandelier(position, entry, side, atr)
        state = position['chandelier_state']
        if valid(old_stop):
            state['stop'] = state['confirmed_stop'] = _tighten(side, state['stop'], float(old_stop))
        # Existing positions start observing now; do not backfill old peak paths.
        state['last_closed_bar'] = int(now_ms//60000)*60000-60000
    if state.get('pending'):
        _publish(position, state)
        return state['reason']
    try:
        opened = float(position['open_timestamp'])*1000
    except (KeyError, TypeError, ValueError):
        return None
    if not valid(opened) or now_ms < opened:
        return None
    if frame is not None and frame.attrs.get('timeframe_ms', 60000) == 60000:
        closed = closed_entry_candles(frame)
        for _, row in closed.iterrows():
            try:
                bar = float(row['timestamp'])
                o, c, h, l, atr = (float(row[k]) for k in ('open','close','high','low','atr'))
                middle = float(row.get('kc_middle', row.get('ema_20', float('nan'))))
            except (KeyError, TypeError, ValueError):
                continue
            if (not all(valid(v) for v in (bar,o,c,h,l,atr)) or bar % 60000 != 0
                    or not l <= min(o,c) <= max(o,c) <= h
                    or bar+60000 > now_ms or bar+60000 <= opened
                    or bar <= state['last_closed_bar']):
                continue
            bucket = state['quote_bars'].get(str(int(bar)), {})
            # Complete post-entry candles can safely supply their full extrema.
            high = h if bar >= opened else max(entry, c, bucket.get('high', entry))
            low = l if bar >= opened else min(entry, c, bucket.get('low', entry))
            state['confirmed_high'] = max(state['confirmed_high'], high)
            state['confirmed_low'] = min(state['confirmed_low'], low)
            extreme = state['confirmed_high'] if side=='LONG' else state['confirmed_low']
            candidate = _tighten(side, entry-sign*1.5*atr, extreme-sign*1.5*atr)
            if not valid(candidate):
                continue
            stop = _tighten(side, state['confirmed_stop'], candidate, bucket.get('stop', state['confirmed_stop']))
            state.update(confirmed_stop=stop, atr=atr, last_closed_bar=bar)
            state['stop'] = _tighten(side, state['stop'], stop)
            # Equal/touch is not a breach. The middle exit needs a crossing body.
            reason = STOP_REASON if sign*(c-stop) < 0 else None
            if (reason is None and bar >= opened and valid(middle)
                    and sign*(o-middle) >= 0 and sign*(c-middle) < 0):
                reason = MIDDLE_REASON
            state['quote_bars'] = {k:v for k,v in state['quote_bars'].items() if float(k)>bar}
            if reason:
                state.update(pending=True, reason=reason, trigger_bar=bar,
                             trigger_close=c, trigger_stop=stop)
                _publish(position, state)
                return reason
    # Account ticker updates may observe peaks, but cannot authorize a new exit.
    state['highest_price'] = max(state['highest_price'], state['confirmed_high'], float(price))
    state['lowest_price'] = min(state['lowest_price'], state['confirmed_low'], float(price))
    extreme = state['highest_price'] if side=='LONG' else state['lowest_price']
    candidate = _tighten(side, entry-sign*1.5*state['atr'], extreme-sign*1.5*state['atr'])
    if valid(candidate):
        state['stop'] = _tighten(side, state['stop'], candidate)
    key = str(int(now_ms//60000)*60000)
    bucket = state['quote_bars'].setdefault(key, dict(high=float(price),low=float(price),stop=state['stop']))
    bucket.update(high=max(bucket['high'],float(price)),low=min(bucket['low'],float(price)),
                  stop=_tighten(side,bucket['stop'],state['stop']))
    # Bound persisted quote history; missing older bars still use confirmed OHLC.
    for old in sorted(state['quote_bars'],key=int)[:-240]:
        del state['quote_bars'][old]
    _publish(position, state)
    return None
