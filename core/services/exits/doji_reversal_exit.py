"""First live adverse body after a confirmed doji; no REST or scan lock."""
import copy
import math
import time

from core.services.exits.dual_track_exit_service import POLICY

REASON = 'EXIT_DOJI_FIRST_ADVERSE_BODY'
DOJI_BODY_RATIO = .25
ADVERSE_BODY_ATR = .5


def observe_doji_reversal(position, frame, price, stamp):
    """Return a durable position-bound exit; never infer tick order from wicks."""
    try:
        side = position['side']
        opened = float(position['open_timestamp'])
        identity = [side, opened, float(position['entry_price']),
                    abs(float(position.get('qty', position.get('quantity', 0))))]
        if side not in ('LONG', 'SHORT') or not all(math.isfinite(v) and v > 0 for v in identity[1:]+[price,stamp]):
            return None
        if stamp < opened*1000:
            return None
        state = position.get('doji_reversal_state') or {}
        if state.get('identity') != identity:
            state = {'identity':identity}
        if stamp < state.get('last_ms', 0):
            return None
        if state.get('pending'):
            return REASON
        if frame is None or len(frame) < 2:
            return None
        import numpy as np
        previous, live = frame.iloc[-2], frame.iloc[-1]
        if frame.attrs.get('timeframe_ms',60000) != 60000:
            return None
        if not isinstance(previous.get('is_closed'),(bool,np.bool_)) or not previous['is_closed']:
            return None
        if not isinstance(live.get('is_closed'),(bool,np.bool_)) or live['is_closed']:
            return None
        bar = math.floor(stamp/60000)*60000
        if float(live['timestamp']) != bar or float(previous['timestamp']) != bar-60000:
            return None
        # Previous doji must have completed after entry; no retrospective entry-bar exit.
        if bar < opened*1000:
            return None
        o,h,l,c = (float(previous[key]) for key in ('open','high','low','close'))
        opening, atr = float(live['open']), float(previous['atr'])
        if not all(math.isfinite(v) and v > 0 for v in (o,h,l,c,opening,atr)):
            return None
        if h <= l or not l <= min(o,c) <= max(o,c) <= h:
            return None
        body = abs(c-o)
        if body > DOJI_BODY_RATIO*(h-l) and not math.isclose(body,DOJI_BODY_RATIO*(h-l),rel_tol=1e-12):
            return None
        adverse = (price-opening) * (1 if side=='SHORT' else -1)
        state.update(last_ms=stamp)
        position['doji_reversal_state'] = state
        if adverse < ADVERSE_BODY_ATR*atr and not math.isclose(adverse,ADVERSE_BODY_ATR*atr,rel_tol=1e-12):
            return None
        state.update(pending=True,bar=bar,opening=opening,atr=atr,trigger_price=price)
        position['closed_exit_state'] = dict(policy=POLICY,pending=True,reason=REASON)
        return REASON
    except (KeyError, TypeError, ValueError, OverflowError, IndexError):
        return None


async def enforce_doji_reversal(engine, symbol, price, quote_ms=None):
    """Use cached original live open and prior closed ATR; retry without candles."""
    if not getattr(engine,'is_running',False):
        return False
    position = engine.account.positions.get(symbol)
    if not position or position.get('entry_mode') != 'CHANNEL_SWING':
        return False
    try:
        stamp = float(quote_ms) if quote_ms is not None else time.time()*1000
        price = float(price)
        if not math.isfinite(stamp) or not 0 <= time.time()-stamp/1000 <= 5 or not math.isfinite(price) or price <= 0:
            return False
    except (TypeError,ValueError,OverflowError):
        return False
    meta = engine.account.position_meta.setdefault(symbol,{})
    if 'doji_reversal_state' not in position and 'doji_reversal_state' in meta:
        position['doji_reversal_state'] = copy.deepcopy(meta['doji_reversal_state'])
    reason = observe_doji_reversal(position,getattr(engine,'_channel_exit_frames',{}).get(symbol),price,stamp)
    if not reason:
        return False
    position['closed_exit_state'] = dict(policy=POLICY,pending=True,reason=reason)
    for key in ('doji_reversal_state','closed_exit_state'):
        meta[key] = copy.deepcopy(position[key])
    engine.account.save_state()
    from core.services.exits.hard_stop_service import enforce_hard_stop
    if await enforce_hard_stop(engine.account,symbol,price):
        return True
    if engine.account.positions.get(symbol) is not position:
        return False
    await engine.account.close_position(symbol,price,reason,is_manual=True)
    return True
