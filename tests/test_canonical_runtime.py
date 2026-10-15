import pytest
import pandas as pd
import numpy as np
from unittest.mock import AsyncMock, patch, MagicMock
from core.engine import TradingEngine

@pytest.fixture
def engine():
    eng = TradingEngine()
    eng.account = MagicMock()
    eng.account.positions = {}
    eng.account.trades = []
    eng.account.open_position = AsyncMock(return_value=True)
    eng.tickers = {'1000PEPE/USDT': 13}
    return eng

def make_frame(rows):
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    for col in ['open', 'high', 'low', 'close', 'kc_upper', 'kc_middle', 'kc_lower', 'atr']:
        if col in df: df[col] = df[col].astype(float)
    return df



@pytest.mark.parametrize('symbol', ['1000PEPE/USDT', '龙虾/USDT'])
@pytest.mark.anyio
async def test_canonical_formation_matrix(engine, symbol):
    engine.tickers = {}
    
    # 1. K1 body < 20%
    rows = [
        {'timestamp': 1000000, 'open': 10, 'high': 12, 'low': 9, 'close': 10.1, 'is_closed': True, 'kc_upper': 10.5, 'kc_middle': 10, 'kc_lower': 9.5, 'atr': 1},
        {'timestamp': 1060000, 'open': 10.1, 'high': 13, 'low': 10.0, 'close': 12, 'is_closed': True, 'kc_upper': 11.5, 'kc_middle': 11, 'kc_lower': 10.5, 'atr': 1},
        {'timestamp': 1120000, 'open': 12, 'high': 13, 'low': 10.5, 'close': 12.5, 'is_closed': False, 'kc_upper': 12.5, 'kc_middle': 12, 'kc_lower': 11.5, 'atr': 1},
    ]
    frame = make_frame(rows)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    res = await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    assert res is None, "K1 body < 20% should be blocked"

    # 2. K2 body < 20%
    rows[0]['close'] = 11 # Fix K1
    rows[1]['close'] = 10.2 # K2 body < 20%
    frame = make_frame(rows)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    res = await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    assert res is None, "K2 body < 20% should be blocked"
    
    # 3. own-KC (K2 not outside KC)
    rows[1]['close'] = 11.4 # Body ok, but <= kc_upper (11.5)
    frame = make_frame(rows)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    res = await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    assert res is None, "K2 not outside KC should be blocked"

    # 4. non-adjacent
    rows[1]['close'] = 12 # Fix K2
    rows[1]['timestamp'] = 1120000 # Gap of 120000ms
    rows[2]['timestamp'] = 1180000
    frame = make_frame(rows)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    res = await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    assert res is None, "Non-adjacent K1/K2 should be blocked"


