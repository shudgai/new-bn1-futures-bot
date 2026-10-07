"""Restored structural exits do not wait for qualification of a new position."""
REASON = 'Channel Swing EXIT_CONFIRMED_SWING_STRUCTURE'

async def prepare_structure_close(account, symbol, price, reason):
    return True
