"""Retired doji compatibility surface; no strategy exit authority."""
from core.services.exits.dual_track_exit_service import POLICY

REASON = 'EXIT_DOJI_FIRST_ADVERSE_BODY'
DOJI_BODY_RATIO = .25
ADVERSE_BODY_ATR = .5


def observe_doji_reversal(position, frame, price, stamp):
    """Retired: only the new peak-trailing policy may authorize strategy exits."""
    return None


async def enforce_doji_reversal(engine, symbol, price, quote_ms=None):
    """Retired compatibility adapter, with no order or state side effects."""
    return False
