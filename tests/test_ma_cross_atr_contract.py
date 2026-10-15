"""Active MA entry and one-minute chandelier contract, with offline transport."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_service import check_entry_signals, supported_entry_reason
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
from core.services.exits.entry_atr_protection import initialize_atr_protection, atr_exit_reason
from core.services.exits.profit_protection_service import ProfitProtectionExitStrategy
from core.services.symbol_runner import process_single_symbol_runner
from core.services.exit_service import STOP_REASON, MIDDLE_REASON


@pytest.fixture(autouse=True)
def isolated_clock_and_logs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data').mkdir()
    monkeypatch.setattr('core.services.exit_service.time.time', lambda: 421.)


def candles(side='LONG', body=1.):
    f = pd.DataFrame([dict(timestamp=60000*(i+1), open=100., close=100.,
                          high=103., low=99., ma3=100., ma15=100., atr=2.,
                          kc_middle=100., kc_upper=110., kc_lower=90.,
                          is_closed=i<6) for i in range(7)])
    f.loc[5, ['open','close','ma3']] = [102.-body,102.,101.]
    if side == 'SHORT':
        original = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15')]:
            f[a] = 200-original[b]
    f.attrs['timeframe_ms'] = 60000
    return f


def position(side):
    p = dict(side=side, entry_price=100., qty=.1, open_timestamp=360., entry_mode='CHANNEL_SWING')
    initialize_atr_protection(p,100.,side,2.)
    return p


def exit_frame(side, opened=101., close=101., high=102., low=100.):
    f = pd.DataFrame([dict(timestamp=360000,open=opened,close=close,high=high,low=low,
                          atr=2.,kc_middle=100.,ma3=101.,ma15=100.,is_closed=True)])
    if side == 'SHORT':
        original = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15')]:
            f[a] = 200-original[b]
    f.attrs['timeframe_ms'] = 60000
    return f


























