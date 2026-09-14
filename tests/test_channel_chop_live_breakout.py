"""Live range breaks share scan, quote, reentry and account validation."""
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_diagnostics_service import entry_diagnostics
from core.services.strategies.outer_strategy import aligned_entry
from core.services.swing_service import channel_chop_state, channel_swing_action
from test_channel_swing_execution import SYMBOL, _execution_engine


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def range_market(side: str, kind: str = 'low_momentum') -> pd.DataFrame:
    frame = pd.DataFrame({
        'open': [100.] * 25, 'close': [100.] * 25,
        'high': [100.2] * 25, 'low': [99.8] * 25,
        'ma3': [100.] * 25, 'ma15': [100.] * 25,
        'kc_middle': [100.] * 25, 'kc_upper': [101.] * 25,
        'kc_lower': [99.] * 25, 'atr': [1.] * 25,
        'volume': [100.] * 25, 'vol_ma_20': [100.] * 25,
        'timestamp': [i * 60_000 for i in range(25)],
    })
    if kind == 'compression':
        frame.loc[:22, ['kc_upper', 'kc_lower']] = [103., 97.]
        frame.loc[20:23, 'kc_middle'] = [99.4, 99.6, 99.8, 100.]
    price = 101.01 if side == 'LONG' else 98.99
    frame.loc[24, ['close', 'high', 'low']] = [price, max(price, 100.2), min(price, 99.8)]
    return frame


def flat_engine(frame: pd.DataFrame, side: str) -> TradingEngine:
    engine = _execution_engine(frame, side, True)
    engine.account.positions.clear()
    engine.account.save_state = lambda: None
    engine._channel_chop_state = channel_chop_state
    engine._abnormal_market_entry_allowed = lambda *a, **k: True
    engine.tickers[SYMBOL] = float(frame.iloc[-1]['close'])
    engine.is_running = True
    engine._channel_entry_quote_times = {SYMBOL: 1441.}
    return engine


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('kind', ['low_momentum', 'compression'])
def test_first_live_break_enters_without_closed_confirmation(side: str, kind: str) -> None:
    frame = range_market(side, kind)
    price = float(frame.iloc[-1]['close'])
    assert channel_chop_state(frame)[kind]
    for decision in (aligned_entry(frame, price), channel_swing_action(frame, price)):
        assert decision == dict(action='ENTER', side=side, reason='KC_CHOP_BREAKOUT_' + side)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['inside', 'touch', 'already_outside', 'nan_price', 'nan_rail', 'bad_rails', 'bad_open', 'bad_atr'])
def test_only_a_current_valid_cross_can_enter(side: str, case: str) -> None:
    frame = range_market(side)
    rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
    price = float(frame.iloc[-1]['close'])
    if case == 'inside':
        price = 100.
    elif case == 'touch':
        price = float(frame.iloc[-1][rail])
    elif case == 'already_outside':
        frame.loc[24, 'open'] = price
    elif case == 'nan_price':
        price = float('nan')
    elif case == 'nan_rail':
        frame.loc[24, rail] = float('nan')
    elif case == 'bad_rails':
        frame.loc[24, ['kc_upper', 'kc_lower']] = [98., 102.]
    elif case == 'bad_open':
        frame.loc[24, 'open'] = 0.
    else:
        frame.loc[23, 'atr'] = float('nan')
    assert aligned_entry(frame, price)['action'] == 'WAIT'


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_scan_snapshot_diagnostics_and_order_agree(side: str, monkeypatch) -> None:
    frame = range_market(side)
    engine = flat_engine(frame, side)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    monkeypatch.setattr('core.engine.time.time', lambda: 1441.)
    engine._channel_intrabar_ready = TradingEngine._channel_intrabar_ready.__get__(engine)
    # A stale terminal label must not veto a new break out of consolidation.
    engine._channel_terminal_blocked = lambda *a, **k: True
    engine._channel_terminal_market = lambda *_: True
    price = engine.tickers[SYMBOL]
    assert entry_diagnostics(engine, SYMBOL, frame, price, 1441.)['reason'] == 'KC_ENTRY_READY'
    assert await engine._fresh_channel_entry_snapshot(SYMBOL, side)
    await engine._process_single_symbol(SYMBOL, 1441., None, False)
    assert [(e[0], e[2]) for e in engine.account.events] == [('open', side)], engine.account.logs
    assert not await engine._execute_confirmed_channel_break(SYMBOL, frame, price, side)
    assert len(engine.account.events) == 1


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_return_inside_after_scan_cancels_order_without_latching(side: str, monkeypatch) -> None:
    frame = range_market(side)
    engine = flat_engine(frame, side)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    monkeypatch.setattr('core.engine.time.time', lambda: 1441.)
    snapshot = await engine._fresh_channel_entry_snapshot(SYMBOL, side)
    assert snapshot
    signal = dict(side=side, score=100, entry_mode='CHANNEL_SWING', action='ENTER_MARKET',
                  signal_code='KC_CHOP_BREAKOUT_' + side, live_outer=True,
                  candidate_bar_id=engine._channel_candidate_bar_id(frame), reason='range break')
    engine.tickers[SYMBOL] = 100.
    assert not await engine._place_structured_entry(SYMBOL, signal, snapshot['price'], snapshot)
    assert not engine.account.events
    engine.tickers[SYMBOL] = snapshot['price']
    assert await engine._place_structured_entry(SYMBOL, signal, snapshot['price'])


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('same_bar', [False, True])
def test_post_peak_close_allows_only_a_later_new_live_break(side: str, same_bar: bool) -> None:
    frame = range_market(side)
    exit_bar = frame.iloc[-1 if same_bar else -2]['timestamp']
    blocked = TradingEngine._channel_peak_exit_reentry_blocked(
        'ENTER', False, side, frame, dict(exit_bar_id=exit_bar), SYMBOL,
        live_price=float(frame.iloc[-1]['close']),
    )
    assert blocked is same_bar


@pytest.mark.anyio
@pytest.mark.parametrize('blocked', ['daily', 'balance', 'abnormal', 'slots', 'cooldown', 'same_bar', 'stale_quote', 'unsafe_price'])
async def test_live_break_preserves_risk_checks(blocked: str, monkeypatch) -> None:
    frame = range_market('LONG')
    engine = flat_engine(frame, 'LONG')
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    monkeypatch.setattr('core.engine.time.time', lambda: 1441.)
    engine._channel_intrabar_ready = TradingEngine._channel_intrabar_ready.__get__(engine)
    if blocked == 'balance':
        engine.account.get_available_balance = lambda: 0.
    elif blocked == 'abnormal':
        engine._abnormal_market_entry_allowed = lambda *a, **k: False
    elif blocked == 'slots':
        monkeypatch.setattr('core.engine.MAX_SLOTS', 1)
        engine.account.pending_limit_orders = {'OTHER/USDT': {}}
    elif blocked == 'cooldown':
        engine._channel_stop_cooldown_remaining = lambda *_: 300.
        engine._channel_strong_trend = lambda *_: False
    elif blocked == 'same_bar':
        engine.account.last_closed_at = {SYMBOL: 1440.5}
    elif blocked == 'stale_quote':
        engine._channel_entry_quote_times[SYMBOL] = 1430.
    elif blocked == 'unsafe_price':
        engine._execution_price_is_safe = AsyncMock(return_value=False)
    assert not await engine._execute_confirmed_channel_break(
        SYMBOL, frame, engine.tickers[SYMBOL], 'LONG', daily_halt=blocked == 'daily',
    )
    assert not engine.account.events


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case', ['fresh', 'stale_quote', 'stale_frame'])
async def test_quote_callback_enters_before_the_next_scan(side: str, case: str, monkeypatch) -> None:
    frame = range_market(side)
    engine = flat_engine(frame, side)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    monkeypatch.setattr('core.engine.SYMBOL_ROTATION_ENABLED', False)
    monkeypatch.setattr('core.engine.time.time', lambda: 1441.)
    engine._channel_intrabar_ready = TradingEngine._channel_intrabar_ready.__get__(engine)
    if case == 'stale_quote':
        engine._channel_entry_quote_times[SYMBOL] = 1430.
    elif case == 'stale_frame':
        frame.loc[24, 'timestamp'] -= 60_000
    engine._channel_exit_frames = {SYMBOL: frame}
    await engine._channel_quote_pivot_entry(SYMBOL, engine.tickers[SYMBOL])
    assert len(engine.account.events) == int(case == 'fresh'), engine.account.logs


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_final_quote_recheck_cancels_return_inside(side: str, monkeypatch) -> None:
    frame = range_market(side)
    engine = flat_engine(frame, side)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    monkeypatch.setattr('core.engine.time.time', lambda: 1441.)
    engine._channel_intrabar_ready = TradingEngine._channel_intrabar_ready.__get__(engine)
    observed = []

    async def safety_check(*_args) -> bool:
        observed.append(True)
        engine.tickers[SYMBOL] = 100.
        return True

    engine._execution_price_is_safe = safety_check
    assert not await engine._execute_confirmed_channel_break(SYMBOL, frame, engine.tickers[SYMBOL], side)
    assert observed
    assert not engine.account.events


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('mode', ['outer_cycle', 'next_breakout', 'same_side_special_k', 'trend_same_side'])
async def test_closed_ticket_reentry_uses_the_same_live_break(side: str, mode: str, monkeypatch) -> None:
    frame = range_market(side)
    frame.loc[24, 'open'] = 100.9 if side == 'LONG' else 99.1
    engine = flat_engine(frame, side)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [SYMBOL])
    monkeypatch.setattr('core.engine.time.time', lambda: 1441.)
    engine.account.channel_profit_reentries = {SYMBOL: dict(
        token='range-test', phase='closed', side=side, mode=mode,
        exit_bar_id=float(frame.iloc[-2]['timestamp']), requires_pullback=False,
    )}
    engine.account.trades = []
    await engine._try_profit_reentry(SYMBOL, frame, engine.tickers[SYMBOL], False)
    assert [(e[0], e[2]) for e in engine.account.events] == [('open', side)], engine.account.logs
