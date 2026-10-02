"""Initial stop compatibility; strategy exits belong to real-time peak trailing."""
import math
from core.services.exits.peak_trailing_exit import POLICY, STATE_KEYS, RETIRED_KEYS
STOP_REASON = "EXIT_INITIAL_ATR_HARD_STOP"
MIDDLE_REASON = "EXIT_MIDDLE"


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
    for key in RETIRED_KEYS:
        position.pop(key, None)


def chandelier_exit_reason(position, price, frame=None, *, now_ms=None):
    from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
    return DualTrackExitStrategy().evaluate_exit(position, frame, current_price=price)


def exhaustion_exit_reason(position, closed, price):
    # User requested to completely remove exhaustion exit
    return None
