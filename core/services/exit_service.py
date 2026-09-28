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


def initialize_chandelier(position, entry_price, side, atr, initial_stop=None):
    if side not in ("LONG", "SHORT") or not all(valid(v) for v in (entry_price, atr)):
        raise ValueError("Invalid chandelier inputs")
    entry_price, atr = float(entry_price), float(atr)
    sign = 1 if side == "LONG" else -1
    stop = float(initial_stop) if initial_stop is not None else entry_price - sign * 1.5 * atr
    if not valid(stop) or sign * (entry_price-stop) <= 0:
        raise ValueError("Invalid chandelier stop")
    position.update(entry_atr=atr, atr_sl=stop, atr_tp=0., atr_protection_version=2,
                    sl=stop, tp=0., initial_sl=stop, initial_risk=abs(entry_price-stop))
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
    from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
    return DualTrackExitStrategy().evaluate_exit(position, frame, current_price=price)


def exhaustion_exit_reason(position, closed, price):
    """Post-entry closed-bar exhaustion; eight-bar local extreme, symmetric.

    Require 1.2 entry ATR profit at both confirmation close and latest quote.
    Pin: a new outer-rail extreme and rejection wick >= 1.5 body.
    Reversal: adverse close outside the rail through the prior favorable
    body's midpoint; the prior bar must have extended outside that rail.
    """
    try:
        side = position['side']
        if side not in ('LONG', 'SHORT') or closed is None or len(closed) < 2:
            return None
        sign = 1 if side == 'LONG' else -1
        entry, atr = float(position['entry_price']), float(position['entry_atr'])
        opened = float(position.get('open_timestamp') or 0)*1000
        rows = closed[closed.timestamp.astype(float) >= opened].tail(9)
        if len(rows) < 2:
            return None
        values = rows[['open', 'high', 'low', 'close', 'kc_upper', 'kc_lower']].astype(float)
        if not all(valid(v) for v in [entry, atr, price] + list(values.to_numpy().flat)):
            return None
        if not ((values.low <= values[['open', 'close']].min(axis=1)) &
                (values.high >= values[['open', 'close']].max(axis=1)) &
                (values.kc_lower < values.kc_upper)).all():
            return None
        current, previous = rows.iloc[-1], rows.iloc[-2]
        if min(sign*(float(current.close)-entry), sign*(price-entry))/atr < 1.2 - 1e-12:
            return None
        body = abs(float(current.close)-float(current.open))
        if side == 'LONG':
            extreme = float(current.high)
            new_extreme = extreme > float(rows.high.iloc[:-1].max())
            outside = extreme > float(current.kc_upper)
            wick = extreme-max(float(current.open),float(current.close))
            reversal_zone = (float(current.close) > float(current.kc_upper)
                             and float(previous.high) > float(previous.kc_upper))
        else:
            extreme = float(current.low)
            new_extreme = extreme < float(rows.low.iloc[:-1].min())
            outside = extreme < float(current.kc_lower)
            wick = min(float(current.open),float(current.close))-extreme
            reversal_zone = (float(current.close) < float(current.kc_lower)
                             and float(previous.low) < float(previous.kc_lower))
        if new_extreme and outside and wick > 0 and wick >= 1.5*body:
            return 'EXIT_EXHAUSTION_PIN_CLOSED'
        midpoint = (float(previous.open)+float(previous.close))/2
        if (reversal_zone and sign*(float(previous.close)-float(previous.open)) > 0
                and sign*(float(current.close)-float(current.open)) < 0
                and sign*(float(current.close)-midpoint) < 0):
            return 'EXIT_EXHAUSTION_REVERSAL_CLOSED'
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None
    return None
