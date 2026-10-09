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
async def test_resurrection_bug_prevented(engine, symbol):
    engine.tickers = {}
    # Tick 1: evaluate K1 and K2 to register them
    rows1 = [
        {'timestamp': 1000000, 'open': 10, 'high': 12, 'low': 9, 'close': 11, 'is_closed': True, 'kc_upper': 10.5, 'kc_middle': 10, 'kc_lower': 9.5, 'atr': 1},
        {'timestamp': 1060000, 'open': 11, 'high': 13, 'low': 10.5, 'close': 12, 'is_closed': True, 'kc_upper': 11.5, 'kc_middle': 11, 'kc_lower': 10.5, 'atr': 1},
        {'timestamp': 1120000, 'open': 12, 'high': 13, 'low': 10.5, 'close': 12.5, 'is_closed': False, 'kc_upper': 12.5, 'kc_middle': 12, 'kc_lower': 11.5, 'atr': 1},
    ]
    frame1 = make_frame(rows1)
    engine._entry_boundary_frame = AsyncMock(return_value=frame1)
    await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    
    # Tick 2: K2 closes, so now it is registered! But wait, K2 needs to be closed.
    rows2 = [
        {'timestamp': 1000000, 'open': 10, 'high': 12, 'low': 9, 'close': 11, 'is_closed': True, 'kc_upper': 10.5, 'kc_middle': 10, 'kc_lower': 9.5, 'atr': 1},
        {'timestamp': 1060000, 'open': 11, 'high': 13, 'low': 10.5, 'close': 12, 'is_closed': True, 'kc_upper': 11.5, 'kc_middle': 11, 'kc_lower': 10.5, 'atr': 1},
        {'timestamp': 1120000, 'open': 12, 'high': 13, 'low': 10.5, 'close': 11, 'is_closed': True, 'kc_upper': 12.5, 'kc_middle': 12, 'kc_lower': 11.5, 'atr': 1}, # Closed opposite
    ]
    frame2 = make_frame(rows2)
    engine._entry_boundary_frame = AsyncMock(return_value=frame2)
    result = await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    assert result is None
    
    # State should be EXPIRED.
    signal = engine.canonical_machine.active_signals.get((symbol, 'LONG'))
    assert signal.state == 'EXPIRED'
    
    # Re-evaluate with same K1/K2, maybe K4 is forming
    rows2.append({'timestamp': 1180000, 'open': 11, 'high': 14, 'low': 11, 'close': 13, 'is_closed': False, 'kc_upper': 13.5, 'kc_middle': 13, 'kc_lower': 12.5, 'atr': 1})
    frame3 = make_frame(rows2)
    engine._entry_boundary_frame = AsyncMock(return_value=frame3)
    result2 = await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    
    # Should STILL be EXPIRED
    assert result2 is None
    signal2 = engine.canonical_machine.active_signals.get((symbol, 'LONG'))
    assert signal2.state == 'EXPIRED'

@pytest.mark.parametrize('symbol', ['1000PEPE/USDT', '龙虾/USDT'])
@pytest.mark.anyio
async def test_order_failure_does_not_consume(engine, symbol):
    engine.tickers = {}
    rows = [
        {'timestamp': 1000000, 'open': 10, 'high': 12, 'low': 9, 'close': 11, 'is_closed': True, 'kc_upper': 10.5, 'kc_middle': 10, 'kc_lower': 9.5, 'atr': 1},
        {'timestamp': 1060000, 'open': 11, 'high': 13, 'low': 10.5, 'close': 12, 'is_closed': True, 'kc_upper': 11.5, 'kc_middle': 11, 'kc_lower': 10.5, 'atr': 1},
        {'timestamp': 1120000, 'open': 12, 'high': 14, 'low': 11, 'close': 13, 'is_closed': False, 'kc_upper': 12.5, 'kc_middle': 12, 'kc_lower': 11.5, 'atr': 1},
    ]
    frame = make_frame(rows)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    snapshot = await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    assert snapshot is not None
    
    engine.account.open_position = AsyncMock(return_value=False)
    with patch('core.engine.TradingEngine._entry_boundary_frame', new_callable=AsyncMock) as mock_ebf:
        mock_ebf.return_value = frame
        await engine._place_structured_entry_locked(symbol, snapshot, 13.0, channel_snapshot=snapshot)
        
    signal = engine.canonical_machine.active_signals.get((symbol, 'LONG'))
    assert signal.state == 'NEW' # Not consumed
    
    # Test Exception
    engine.account.open_position = AsyncMock(side_effect=Exception("API Error"))
    with patch('core.engine.TradingEngine._entry_boundary_frame', new_callable=AsyncMock) as mock_ebf:
        mock_ebf.return_value = frame
        try:
            await engine._place_structured_entry_locked(symbol, snapshot, 13.0, channel_snapshot=snapshot)
        except:
            pass
            
    signal = engine.canonical_machine.active_signals.get((symbol, 'LONG'))
    assert signal.state == 'NEW' # Still not consumed
        
    signal = engine.canonical_machine.active_signals.get((symbol, 'LONG'))
    assert signal.state == 'NEW' # Still not consumed

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

@pytest.mark.parametrize('symbol', ['1000PEPE/USDT', '龙虾/USDT'])
@pytest.mark.anyio
async def test_canonical_execution_matrix(engine, symbol):
    engine.tickers = {}
    
    # Tick 1: Register K1/K2
    rows = [
        {'timestamp': 1000000, 'open': 10, 'high': 12, 'low': 9, 'close': 11, 'is_closed': True, 'kc_upper': 10.5, 'kc_middle': 10, 'kc_lower': 9.5, 'atr': 1},
        {'timestamp': 1060000, 'open': 11, 'high': 13, 'low': 10.5, 'close': 12, 'is_closed': True, 'kc_upper': 11.5, 'kc_middle': 11, 'kc_lower': 10.5, 'atr': 1},
        {'timestamp': 1120000, 'open': 12, 'high': 13, 'low': 10.5, 'close': 12.5, 'is_closed': False, 'kc_upper': 12.5, 'kc_middle': 12, 'kc_lower': 11.5, 'atr': 1},
    ]
    frame = make_frame(rows)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    
    # K3 closed same-dir non-doji expire
    rows[2]['is_closed'] = True
    rows[2]['close'] = 12.5 # same dir, non-doji
    frame = make_frame(rows)
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    res = await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    assert res is None, "K3 closed same-dir should expire"
    assert engine.canonical_machine.active_signals[(symbol, 'LONG')].state == 'EXPIRED'

    # Reset
    engine.canonical_machine.active_signals.clear()
    rows[2]['is_closed'] = False
    engine._entry_boundary_frame = AsyncMock(return_value=make_frame(rows))
    await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    
    # K3 doji -> WAIT_K4
    rows[2]['close'] = 12.05 # doji
    rows[2]['is_closed'] = True
    engine._entry_boundary_frame = AsyncMock(return_value=make_frame(rows))
    res = await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    assert res is None
    assert engine.canonical_machine.active_signals[(symbol, 'LONG')].state == 'WAIT_K4'
    
    # K4 successful entry
    rows.append({'timestamp': 1180000, 'open': 12.05, 'high': 14, 'low': 12, 'close': 13, 'is_closed': False, 'kc_upper': 13.5, 'kc_middle': 13, 'kc_lower': 12.5, 'atr': 1})
    engine._entry_boundary_frame = AsyncMock(return_value=make_frame(rows))
    res = await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    assert res is not None
    assert res['decision']['action'] == 'ENTER'
    
    # K5 expired
    rows[-1]['is_closed'] = True
    engine._entry_boundary_frame = AsyncMock(return_value=make_frame(rows))
    res = await engine._fresh_channel_entry_snapshot(symbol, 'LONG')
    assert res is None
    assert engine.canonical_machine.active_signals[(symbol, 'LONG')].state == 'EXPIRED'

