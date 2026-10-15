"""Long reversal confirmation at both signal and refreshed account boundaries."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from test_entry_e2e_20260928 import candles
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry, long_entry_trend_problem
from core.services.entry_firewall import validate_entry_frame, validate_account_entry


def bearish_bounce():
    f = candles(body=.55)
    f['kc_middle'] = [100.6, 100.5, 100.4, 100.3, 100.2, 100.1]
    f['kc_upper'] = f.kc_middle + .3
    f['kc_lower'] = f.kc_middle - .3
    f['ma15'] = [100.35, 100.3, 100.25, 100.2, 100.15, 100.1]
    return f






@pytest.mark.parametrize('confirmation', ['ma15', 'two_closes', 'ck'])
def test_each_confirmation_is_independently_sufficient(confirmation):
    f = bearish_bounce()
    if confirmation == 'ma15':
        f.loc[3:5, 'ma15'] = [100., 100.05, 100.1]
    elif confirmation == 'two_closes':
        f.loc[4, 'close'] = 100.25
    else:
        for key, base in [('kc_middle', 99.9), ('kc_upper', 100.2), ('kc_lower', 99.6)]:
            f.loc[3:5, key] = [base, base+.05, base+.1]
        f.loc[4, 'close'] = 99.9
    assert long_entry_trend_problem(f) is None








