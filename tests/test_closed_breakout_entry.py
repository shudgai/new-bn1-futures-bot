"""Closed breakout contract and actual scan/revalidation wiring, without orders."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.closed_breakout_entry import evaluate_closed_breakout
from core.services.entry_service import channel_closed_body_break_entry_action, supported_entry_reason
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
from core.services.symbol_runner import process_single_symbol_runner


def candles(side):
    rows = [
        (99, 100, 101, 98),
        (109, 112, 113, 108),
        (112, 114, 115, 111),
        (114, 113, 115, 112),
    ]
    frame = pd.DataFrame([dict(timestamp=(i+1)*60000, open=float(o), close=float(c), high=float(h), low=float(l),
                              kc_upper=110., kc_lower=90., kc_middle=100.+i,
                              ma3=105., ma15=100., atr=2., is_closed=i<3)
                          for i, (o,c,h,l) in enumerate(rows)])
    if side == 'SHORT':
        for key in ('open', 'close', 'kc_middle', 'ma3', 'ma15'):
            frame[key] = 200-frame[key]
        old_high = frame['high'].copy()
        frame['high'] = 200-frame['low']
        frame['low'] = 200-old_high
    frame.attrs['timeframe_ms'] = 60000
    return frame






