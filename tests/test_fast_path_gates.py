import pytest
import pandas as pd
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from core.services.entry_contract import evaluate_entry_contract
from core.services.strategies.outer_strategy import live_body_breakout_side
from core.engine import TradingEngine
from core.paper_account import PaperAccount


def live_body_frame(side="SHORT"):
    live_timestamp = int(time.time() // 60) * 60_000
    rows = []
    for index in range(12):
        rows.append({
            'timestamp': live_timestamp - (12 - index) * 60_000,
            'is_closed': True,
            'open': 100.0,
            'close': 100.1,
            'high': 100.2,
            'low': 99.9,
            'kc_lower': 90.3 - 0.1 * index,
            'kc_middle': 100.3 - 0.1 * index,
            'kc_upper': 110.0,
            'ma5': 101.1 - 0.1 * index,
            'ma15': 102.0,
            'ma3': 101.2 - 0.1 * index,
            'atr': 1.0,
        })
    rows[-1].update(
        open=100.0, close=89.0, high=100.2, low=88.8,
        ma5=100.0, ma15=102.0, ma3=99.7,
    )
    # The shared contract requires a completed rail break followed by a
    # bearish confirmation candle before evaluating this live short trigger.
    rows[-2].update(
        open=90.0, close=89.2, high=90.2, low=89.0,
        ma5=100.5, ma3=100.0,
    )
    rows.append({
        'timestamp': live_timestamp,
        'is_closed': False,
        'open': 90.5,
        'close': 89.9,
        'high': 90.6,
        'low': 89.8,
        'kc_lower': 89.5,
        'kc_middle': 99.0,
        'kc_upper': 110.0,
        'ma5': 96.0,
        'ma15': 103.0,
        'ma3': 99.5,
        'atr': 1.0,
    })
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

def test_abnormal_market_entry_guard_only_rejects_invalid_price_or_stale_quote(engine):
    engine._channel_entry_quote_times = {'LOBSTER/USDT': time.time()}
    assert engine._abnormal_market_entry_allowed(
        'LOBSTER/USDT', 'SHORT', 80.0, 1.0, 100.0, 101.0, 79.0, 80.0,
    )
    assert not engine._abnormal_market_entry_allowed(
        'LOBSTER/USDT', 'SHORT', 0.0, 1.0, 100.0, 101.0, 79.0, 80.0,
    )
    engine._channel_entry_quote_times['LOBSTER/USDT'] = time.time() - 31
    assert not engine._abnormal_market_entry_allowed(
        'LOBSTER/USDT', 'SHORT', 80.0, 1.0, 100.0, 101.0, 79.0, 80.0,
    )
    engine._channel_entry_quote_times.clear()
    assert not engine._abnormal_market_entry_allowed(
        'LOBSTER/USDT', 'SHORT', 80.0, 1.0, 100.0, 101.0, 79.0, 80.0,
    )


@pytest.mark.parametrize(
    ('side', 'entry_code'),
    [
        ('LONG', 'AUTHORIZED_REALTIME_BREAKOUT'),
        ('SHORT', 'AUTHORIZED_BY_TREND_CONTINUATION_SHORT'),
        ('SHORT', 'AUTHORIZED_BY_PEAK_FLIP_SHORT'),
    ],
)
def test_valid_market_data_reaches_account_gate_for_all_entry_types(
    engine, side, entry_code, monkeypatch,
):
    symbol = 'LOBSTER/USDT'
    quote = 102.0 if side == 'LONG' else 98.0
    stamp = int(time.time() // 60) * 60_000
    previous_ma5 = quote - 1.0 if side == 'LONG' else quote + 1.0
    frame = pd.DataFrame([
        dict(
            timestamp=stamp - 60_000,
            is_closed=True,
            open=quote, high=quote + 0.2, low=quote - 0.2, close=quote,
            ma3=quote, ma5=previous_ma5, ma15=quote,
            atr=1.0, kc_upper=quote + 1.0, kc_middle=quote,
            kc_lower=quote - 1.0,
        ),
        dict(
            timestamp=stamp,
            is_closed=False,
            open=quote - 0.2 if side == 'LONG' else quote + 0.2,
            high=quote + 0.2, low=quote - 0.2, close=quote,
            ma3=quote, ma5=quote - 0.1 if side == 'LONG' else quote + 0.1,
            ma15=quote, atr=1.0,
            kc_upper=quote + 1.0, kc_middle=quote, kc_lower=quote - 1.0,
        ),
    ])
    frame.attrs['timeframe_ms'] = 60_000
    decision = dict(
        type=entry_code,
        side=side,
        reason='POST_CLOSE_CONTINUATION',
        confirmation_bar_id=float(stamp),
        breakout_bar_id=float(stamp),
        close_price=quote,
        pair_confirmation_bar_id=None,
        entry_phase='POST_CLOSE_CONTINUATION_ENTRY',
        entry_atr=1.0,
        pending_signal_id=f'{symbol}:{entry_code}:{stamp}:{side}',
    )
    decision['exit_bar_id'] = float(stamp)
    engine.account.pending_limit_orders = {}
    engine.account.daily_loss_limit_hit.return_value = (False, 0.0)
    engine.account.trades = []
    engine.account.get_wallet_balance.return_value = 1_000.0
    engine.account.get_available_balance.return_value = 1_000.0
    engine.account.open_position = AsyncMock(return_value=True)
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *_: 2)
    engine.tickers = {symbol: quote}
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    engine._channel_entry_quote_times = {symbol: time.time()}
    engine._fresh_channel_entry_snapshot = AsyncMock(return_value={
        'frame': frame, 'price': quote, 'decision': decision,
    })
    engine._abnormal_market_entry_allowed = Mock(return_value=True)
    frame.loc[frame.index[-1], 'high'] = quote + 8.0
    frame.loc[frame.index[-1], 'low'] = quote - 8.0
    signal = dict(
        side=side,
        score=100,
        entry_mode='CHANNEL_SWING',
        signal_code=entry_code,
        candidate_bar_id=float(stamp),
    )
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS', [symbol])
    monkeypatch.setattr('core.config.is_entry_disabled', lambda _: False)
    monkeypatch.setattr(
        'core.services.entry_contract.evaluate_entry_contract',
        lambda *_args, **_kwargs: decision,
    )
    monkeypatch.setattr(
        'core.services.pre_entry_space_shadow.record_pre_entry_space_shadow',
        lambda **_kwargs: None,
    )

    result = asyncio.run(
        engine._place_structured_entry_locked(symbol, signal, quote),
    )

    assert result is True
    engine._abnormal_market_entry_allowed.assert_called_once()
    engine.account.open_position.assert_awaited_once()


@pytest.mark.parametrize(
    ('case', 'expected_reason'),
    [
        ('empty_frame', 'BLOCKED_REVALIDATION_FRAME_UNAVAILABLE'),
        ('side_mismatch', 'BLOCKED_REVALIDATED_SIDE_MISMATCH'),
        ('bar_mismatch', 'BLOCKED_CANDIDATE_BAR_CHANGED'),
    ],
)
def test_fresh_snapshot_failures_record_specific_reason(
    engine, case, expected_reason, monkeypatch,
):
    frame = None if case == 'empty_frame' else live_body_frame()
    candidate = {
        'side': 'SHORT' if case == 'side_mismatch' else 'LONG',
        'confirmation_bar_id': 2000 if case == 'bar_mismatch' else 1000,
        'type': 'TRIGGER_A_KC_BREAKOUT',
    }
    engine._entry_boundary_frame = AsyncMock(return_value=frame)
    monkeypatch.setattr(
        'core.services.entry_contract.evaluate_entry_contract',
        lambda *_args, **_kwargs: candidate,
    )

    result = asyncio.run(engine._fresh_channel_entry_snapshot(
        'LOBSTER/USDT', 'LONG', candidate_bar_id=1000,
    ))

    assert result is None
    assert engine._entry_gate_diagnostics[
        ('LOBSTER/USDT', 'LONG', 'ENTRY_REVALIDATION')
    ][1] == expected_reason


def test_last_closed_at_does_not_create_a_trigger_without_price_action():
    frame = live_body_frame()
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'low']] = [
        100.0, 100.0, 100.2, 99.8,
    ]
    frame.loc[frame.index[-2], ['open', 'close', 'high', 'low']] = [
        100.0, 100.1, 100.2, 99.9,
    ]
    account = SimpleNamespace(
        positions={},
        last_closed_at={'LOBSTER/USDT': frame.iloc[-1].timestamp / 1000.0},
    )
    diagnostics = {}
    decision = evaluate_entry_contract(
        frame, 100.0, account=account, symbol='LOBSTER/USDT',
        diagnostics=diagnostics,
    )
    assert decision is None
    assert diagnostics.get('reason') == 'WAIT_DUAL_TRACK_TRIGGER'


def test_older_last_closed_at_does_not_create_a_trigger_without_price_action():
    frame = live_body_frame()
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'low']] = [
        100.0, 100.0, 100.2, 99.8,
    ]
    frame.loc[frame.index[-2], ['open', 'close', 'high', 'low']] = [
        100.0, 100.1, 100.2, 99.9,
    ]
    diagnostics = {}
    account = SimpleNamespace(
        positions={},
        last_closed_at={'LOBSTER/USDT': frame.iloc[-1].timestamp / 1000.0 - 60.0},
    )
    decision = evaluate_entry_contract(
        frame, 100.0, account=account, symbol='LOBSTER/USDT',
        diagnostics=diagnostics,
    )
    assert decision is None
    assert diagnostics.get('reason') == 'WAIT_DUAL_TRACK_TRIGGER'


def test_final_safety_gates(engine):
    frame = live_body_frame()
    decision = evaluate_entry_contract(
        frame, 89.4, code='KC_LIVE_BODY_BREAKOUT_SHORT', symbol='SYM',
    )
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
        assert asyncio.run(engine._place_structured_entry_locked(sym, signal, 89.4)) is False
        engine.account.positions = {}
        engine.account.pending_limit_orders = {sym: {}}
        assert asyncio.run(engine._place_structured_entry_locked(sym, signal, 89.4)) is False
        engine.account.pending_limit_orders = {}
        engine.account.positions = {sym: {'side': 'SHORT'}}
        assert asyncio.run(engine._place_structured_entry_locked(sym, signal, 89.4)) is False
        engine.account.positions = {}
        with patch('core.config.is_entry_disabled', return_value=True):
            assert asyncio.run(engine._place_structured_entry_locked(sym, signal, 89.4)) is False
    engine._fresh_channel_entry_snapshot.assert_not_awaited()


def test_no_double_order(engine, monkeypatch):
    sym = 'SYM'
    frame = live_body_frame()
    decision = evaluate_entry_contract(
        frame, 89.4, code='KC_LIVE_BODY_BREAKOUT_SHORT',
        account=engine.account, symbol=sym,
    )
    assert decision is not None
    engine.account.trades.append({
        'symbol': sym, 'action': 'OPEN_SHORT',
        'entry_snapshot': {'pending_signal_id': decision['pending_signal_id']},
    })
    diagnostics = {}
    assert evaluate_entry_contract(
        frame, 89.4, code='KC_LIVE_BODY_BREAKOUT_SHORT',
        account=engine.account, symbol=sym,
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
