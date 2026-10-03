import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock
from core.engine import TradingEngine

def make_frame(side, K3_open, K3_price, edge=0.049):
    sign = 1 if side == 'LONG' else -1
    data = [
        {'timestamp': 1000000.0, 'open': edge - sign*0.001, 'high': edge+0.01, 'low': edge-0.01, 'close': edge + sign*0.001, 'atr': 0.001, 'kc_upper': edge, 'kc_lower': edge, 'ma5': edge-sign*0.001, 'ma15': edge+sign*0.001},
        {'timestamp': 1060000.0, 'open': edge + sign*0.0005, 'high': edge+0.01, 'low': edge-0.01, 'close': edge + sign*0.002, 'atr': 0.001, 'kc_upper': edge, 'kc_lower': edge, 'ma5': edge+sign*0.002, 'ma15': edge-sign*0.002},
        {'timestamp': 1120000.0, 'open': K3_open, 'high': edge+0.01, 'low': edge-0.01, 'close': K3_price, 'atr': 0.001, 'kc_upper': edge, 'kc_lower': edge, 'ma5': edge, 'ma15': edge}
    ]
    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = 60000
    df['is_closed'] = [True, True, False]
    return df

@pytest.fixture
def mock_engine():
    engine = TradingEngine.__new__(TradingEngine)
    engine.account = MagicMock()
    engine.account.positions = {}
    engine.account.pending_limit_orders = {}
    engine.account.trades = []
    engine.account.daily_loss_limit_hit = lambda: (False, "")
    engine.account.get_wallet_balance = lambda: 1000.0
    engine.account.get_available_balance = lambda: 1000.0
    engine.symbol_rotation = MagicMock()
    engine.symbol_rotation.get_dynamic_leverage = lambda s, c: 5.0
    engine._half_wallet_entry_margin = lambda w, a, l: 50.0
    engine._full_wallet_entry_margin = lambda w, a, l: 1000.0
    engine.tickers = {}
    engine.account.open_position = AsyncMock(return_value=True)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    return engine

@pytest.mark.anyio
async def test_order_boundary_long_red(mock_engine):
    mock_engine.tickers = {'龙虾/USDT': 0.0490}
    async def mock_boundary_frame_red(symbol):
        return make_frame('LONG', 0.050, 0.049, edge=0.048)
    mock_engine._entry_boundary_frame = mock_boundary_frame_red
    
    signal_long = {'entry_mode': 'CHANNEL_SWING', 'side': 'LONG', 'signal_code': 'KC_3BAR_CONFIRM_LONG', 'candidate_bar_id': 1120000.0}
    opened = await mock_engine._place_structured_entry_locked('龙虾/USDT', signal_long, 0.049)
    assert not opened
    assert mock_engine.account.open_position.call_count == 0

@pytest.mark.anyio
async def test_order_boundary_long_neutral(mock_engine):
    mock_engine.tickers = {'龙虾/USDT': 0.050}
    async def mock_boundary_frame_neutral_l(symbol):
        return make_frame('LONG', 0.050, 0.050, edge=0.048)
    mock_engine._entry_boundary_frame = mock_boundary_frame_neutral_l
    signal_long = {'entry_mode': 'CHANNEL_SWING', 'side': 'LONG', 'signal_code': 'KC_3BAR_CONFIRM_LONG', 'candidate_bar_id': 1120000.0}
    opened = await mock_engine._place_structured_entry_locked('龙虾/USDT', signal_long, 0.050)
    assert not opened
    assert mock_engine.account.open_position.call_count == 0

@pytest.mark.anyio
async def test_order_boundary_short_green(mock_engine):
    mock_engine.tickers = {'龙虾/USDT': 0.051}
    async def mock_boundary_frame_green(symbol):
        return make_frame('SHORT', 0.050, 0.051, edge=0.052)
    mock_engine._entry_boundary_frame = mock_boundary_frame_green
    signal_short = {'entry_mode': 'CHANNEL_SWING', 'side': 'SHORT', 'signal_code': 'KC_3BAR_CONFIRM_SHORT', 'candidate_bar_id': 1120000.0}
    opened = await mock_engine._place_structured_entry_locked('龙虾/USDT', signal_short, 0.051)
    assert not opened
    assert mock_engine.account.open_position.call_count == 0

@pytest.mark.anyio
async def test_order_boundary_short_neutral(mock_engine):
    mock_engine.tickers = {'龙虾/USDT': 0.050}
    async def mock_boundary_frame_neutral_s(symbol):
        return make_frame('SHORT', 0.050, 0.050, edge=0.052)
    mock_engine._entry_boundary_frame = mock_boundary_frame_neutral_s
    signal_short = {'entry_mode': 'CHANNEL_SWING', 'side': 'SHORT', 'signal_code': 'KC_3BAR_CONFIRM_SHORT', 'candidate_bar_id': 1120000.0}
    opened = await mock_engine._place_structured_entry_locked('龙虾/USDT', signal_short, 0.050)
    assert not opened
    assert mock_engine.account.open_position.call_count == 0
