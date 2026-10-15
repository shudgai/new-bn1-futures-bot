"""Momentum signals reach the real execution/account route with an offline exchange."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_service import check_entry_signals, supported_entry_reason
from test_testnet_account import FakeTestnetExchange


def candles(side, continuation):
    rows = [dict(timestamp=(i+1)*60000, open=99., close=99., high=99.5, low=98.5,
                 kc_upper=100., kc_lower=96., kc_middle=98., ma3=99., ma15=98.,
                 atr=2., volume=100., is_closed=i<3) for i in range(4)]
    if continuation:
        rows[1].update(open=100., close=100.5, high=100.7, low=99.9)
    rows[2].update(open=100., close=101., high=101.2, low=99.8)
    rows[3].update(open=101., close=101.1, high=101.2, low=100.9)
    frame = pd.DataFrame(rows)
    if side == 'SHORT':
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle'),
                    ('ma3','ma3'),('ma15','ma15')]:
            frame[a] = 200-pd.DataFrame(rows)[b]
    frame.attrs['timeframe_ms'] = 60000
    return frame






@pytest.mark.parametrize('code', [None,'ENTER_UNKNOWN_LONG','ENTER_FIRST_BREAKOUT_LONG_extra',{},''])
def test_unknown_codes_remain_blocked(code):
    assert not supported_entry_reason(code,'LONG')
