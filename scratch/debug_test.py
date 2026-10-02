import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing, STATE_KEY

def get_position(side='LONG', entry_price=100.0, qty=1.0, entry_atr=2.0, leverage=1.0):
    pos = {
        'side': side,
        'open_timestamp': 60000,
        'entry_price': entry_price,
        'qty': qty,
        'leverage': leverage,
        'margin': entry_price * qty / leverage,
        'entry_atr': entry_atr,
        STATE_KEY: {}
    }
    return pos

import sys
import core.services.exits.peak_trailing_exit
core.services.exits.peak_trailing_exit.PROFIT_FLOOR_ENABLED = True
core.services.exits.peak_trailing_exit.PROFIT_FLOOR_ARM_ATR = 2.0
core.services.exits.peak_trailing_exit.PROFIT_FLOOR_LOCK_ATR = 1.0

p = get_position('LONG', 100, 1, 2.0)
print(evaluate_peak_trailing(p, 104, 61000, 1.0, fee=0.0, slippage=0.0))
print(p[STATE_KEY])
