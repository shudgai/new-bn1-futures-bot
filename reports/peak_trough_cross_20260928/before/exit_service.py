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
    from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
    return DualTrackExitStrategy().evaluate_exit(position, frame, current_price=price)
