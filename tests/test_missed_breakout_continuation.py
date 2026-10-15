"""Missed initial breakout can continue, without bypassing execution controls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_entry_gate_consistency import frame
from core.services.strategies.pure_trend_v2 import evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry

SYMBOL = '1000PEPE/USDT'


def continuation_frame(side='LONG'):
    f = frame()
    # Four outside closes: the old fresh-consolidation gate must no longer veto.
    for i in f.index[-5:-2]:
        f.loc[i, ['open','close','high','low']] = [100.8,101.05,101.08,100.7]
    if side == 'SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),
                    ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a]=200-original[b]
    return f






