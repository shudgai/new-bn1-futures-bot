import pytest
import pandas as pd
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.services.entry_contract import evaluate_entry_contract
from core.services.strategies.outer_strategy import live_body_breakout_side
from core.engine import TradingEngine
from core.paper_account import PaperAccount


def live_body_frame(side="SHORT"):
    live_timestamp = int(time.time() // 60) * 60_000
    rows = [
        {'timestamp': live_timestamp - 120_000, 'is_closed': True,
         'open': 100.0, 'close': 100.1, 'high': 100.2, 'low': 99.9,
         'kc_lower': 90.0, 'kc_middle': 100.0, 'kc_upper': 110.0, 'atr': 1.0},
        {'timestamp': live_timestamp - 60_000, 'is_closed': True,
         'open': 100.0, 'close': 100.1, 'high': 100.2, 'low': 99.9,
         'kc_lower': 90.0, 'kc_middle': 100.0, 'kc_upper': 110.0, 'atr': 1.0},
        {'timestamp': live_timestamp, 'is_closed': False,
         'open': 100.0, 'close': 89.0, 'high': 100.0, 'low': 89.0,
         'kc_lower': 90.0, 'kc_middle': 100.0, 'kc_upper': 110.0, 'atr': 1.0},
    ]
    if side == "LONG":
        for row in rows:
            row['open'], row['close'] = 200.0 - row['open'], 200.0 - row['close']
            row['high'], row['low'] = 200.0 - row['low'], 200.0 - row['high']
            row['kc_lower'], row['kc_upper'] = 200.0 - row['kc_upper'], 200.0 - row['kc_lower']
            row['kc_middle'] = 200.0 - row['kc_middle']
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame


@pytest.fixture
def engine():
    account = MagicMock(spec=PaperAccount)
    account.positions = {}
    account.trades = []
    account.last_closed_at = {}
    eng = TradingEngine()
    eng.account = account
    eng.tick_buffers = {}
    return eng

def test_range_veto_ordering(engine):
    symbol = 'LOBSTER/USDT'
    frame = live_body_frame()
    frame.loc[2, 'low'] = 70.0
    quote = 89.0
    decision = evaluate_entry_contract(
        frame, quote, account=engine.account, symbol=symbol,
    )
    assert decision is not None

    engine.account.pending_limit_orders = {}
    engine.account.daily_loss_limit_hit.return_value = (False, 0.0)
    engine.account.get_wallet_balance.return_value = 1000.0
    engine.account.get_available_balance.return_value = 1000.0
    engine.account.log = MagicMock()
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args: 2)
    engine.tickers = {symbol: quote}
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    engine._fresh_channel_entry_snapshot = AsyncMock(return_value={
        'frame': frame, 'price': quote, 'decision': decision,
    })
    engine._full_wallet_entry_margin = MagicMock(return_value=100.0)
    signal = {
        'side': 'SHORT', 'score': 100, 'entry_mode': 'CHANNEL_SWING',
        'signal_code': decision['type'],
        'candidate_bar_id': decision['confirmation_bar_id'],
    }
    with (patch('core.engine.DEFAULT_SYMBOLS', {symbol}),
          patch('core.config.is_entry_disabled', return_value=False),
          patch('core.engine.MAX_SLOTS', 1),
          patch('core.engine.ABNORMAL_MARKET_MAX_CANDLE_RANGE_ATR', 5.0)):
        result = asyncio.run(engine._place_structured_entry_locked(
            symbol, signal, quote,
        ))
    assert result is False
    assert 'BLOCKED_ABNORMAL_MARKET_ENTRY' in repr(engine.account.log.call_args)


def test_post_exit_firewall():
    frame = live_body_frame()
    account = SimpleNamespace(
        positions={},
        last_closed_at={'LOBSTER/USDT': frame.iloc[-1].timestamp / 1000.0},
    )
    diagnostics = {}
    decision = evaluate_entry_contract(
        frame, 89.0, account=account, symbol='LOBSTER/USDT',
        diagnostics=diagnostics,
    )
    assert decision is None
    assert diagnostics.get('reason') == 'WAIT_POST_EXIT_NEW_FORMATION'


def test_post_exit_firewall_allowed():
    frame = live_body_frame()
    account = SimpleNamespace(
        positions={},
        last_closed_at={'LOBSTER/USDT': frame.iloc[-1].timestamp / 1000.0 - 60.0},
    )
    decision = evaluate_entry_contract(
        frame, 89.0, account=account, symbol='LOBSTER/USDT',
        diagnostics={},
    )
    assert decision is not None


def test_final_safety_gates(engine):
    frame = live_body_frame()
    decision = evaluate_entry_contract(frame, 89.0, symbol='SYM')
    assert decision is not None
    sym = 'SYM'
    signal = {
        'side': 'SHORT', 'score': 100, 'entry_mode': 'CHANNEL_SWING',
        'signal_code': decision['type'],
        'candidate_bar_id': decision['confirmation_bar_id'],
    }
    engine.account.pending_limit_orders = {}
    engine.account.daily_loss_limit_hit.return_value = (False, 0.0)
    engine._fresh_channel_entry_snapshot = AsyncMock()
    with (patch('core.engine.DEFAULT_SYMBOLS', {sym}),
          patch('core.engine.MAX_SLOTS', 1),
          patch('core.config.is_entry_disabled', return_value=False)):
        engine.account.positions = {'OTHER': {}}
        assert asyncio.run(engine._place_structured_entry_locked(sym, signal, 89.0)) is False
        engine.account.positions = {}
        engine.account.pending_limit_orders = {sym: {}}
        assert asyncio.run(engine._place_structured_entry_locked(sym, signal, 89.0)) is False
        engine.account.pending_limit_orders = {}
        engine.account.positions = {sym: {'side': 'SHORT'}}
        assert asyncio.run(engine._place_structured_entry_locked(sym, signal, 89.0)) is False
        engine.account.positions = {}
        with patch('core.config.is_entry_disabled', return_value=True):
            assert asyncio.run(engine._place_structured_entry_locked(sym, signal, 89.0)) is False
    engine._fresh_channel_entry_snapshot.assert_not_awaited()


def test_no_double_order(engine):
    sym = 'SYM'
    frame = live_body_frame()
    decision = evaluate_entry_contract(
        frame, 89.0, account=engine.account, symbol=sym,
    )
    assert decision is not None
    engine.account.trades.append({
        'symbol': sym, 'action': 'OPEN_SHORT',
        'entry_snapshot': {'pending_signal_id': decision['pending_signal_id']},
    })
    diagnostics = {}
    assert evaluate_entry_contract(
        frame, 89.0, account=engine.account, symbol=sym,
        diagnostics=diagnostics,
    ) is None
    assert diagnostics['reason'] == 'BLOCKED_KC_BREAKOUT_ALREADY_FILLED'


def test_synthetic_boundary_short():
    open_p, atr, kc_lower = 100.0, 2.0, 99.5
    price = 99.0
    frame = pd.DataFrame([
        {'timestamp': 1000, 'is_closed': True, 'open': 100, 'close': 100,
         'kc_lower': 90, 'kc_middle': 100, 'kc_upper': 110, 'atr': atr},
        {'timestamp': 2000, 'is_closed': False, 'open': open_p, 'close': price,
         'kc_lower': kc_lower, 'kc_middle': 104.5, 'kc_upper': kc_lower+10,
         'atr': atr}
    ])
    assert live_body_breakout_side(frame, price) == 'SHORT'
    assert live_body_breakout_side(frame, 100.0 - (0.499 * atr)) is None

def test_synthetic_boundary_long():
    open_p, atr, kc_upper = 100.0, 2.0, 100.5
    price = 101.0
    frame = pd.DataFrame([
        {'timestamp': 1000, 'is_closed': True, 'open': 100, 'close': 100,
         'kc_lower': 90, 'kc_middle': 100, 'kc_upper': 110, 'atr': atr},
        {'timestamp': 2000, 'is_closed': False, 'open': open_p, 'close': price,
         'kc_lower': kc_upper-10, 'kc_middle': 95.5, 'kc_upper': kc_upper,
         'atr': atr}
    ])
    assert live_body_breakout_side(frame, price) == 'LONG'
    assert live_body_breakout_side(frame, 100.0 + (0.499 * atr)) is None
