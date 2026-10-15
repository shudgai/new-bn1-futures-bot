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


def with_entry_history(frame, minimum_rows=10):
    result = frame.copy()
    result.attrs.update(frame.attrs)
    if 'kc_middle' not in result.columns:
        result['kc_middle'] = (
            result['kc_upper'].astype(float) + result['kc_lower'].astype(float)
        ) / 2.0
    missing = max(0, minimum_rows - len(result))
    if not missing:
        return result
    first = result.iloc[0].to_dict()
    first_timestamp = float(first['timestamp'])
    rows = []
    for offset in range(missing, 0, -1):
        row = dict(first)
        row['timestamp'] = first_timestamp - offset * 60_000
        row['is_closed'] = True
        rows.append(row)
    padded = pd.concat([pd.DataFrame(rows), result], ignore_index=True)
    padded.attrs.update(result.attrs)
    return padded


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
            'channel_state': 'DOWN',
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
        'channel_state': 'DOWN',
        'kc_upper_slope': -0.2,
        'kc_lower_slope': -0.2,
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


def test_failed_live_breakout_revalidation_blocks_later_outer_chase(engine):
    symbol = 'LOBSTER/USDT'
    frame = live_body_frame('SHORT')
    engine._entry_boundary_frame = AsyncMock(side_effect=[None, frame])
    engine.tickers = {symbol: 89.4}
    authorized = {
        'pending_signal_id': f'{symbol}:LIVE_BODY:1:SHORT',
    }

    first = asyncio.run(engine._fresh_channel_entry_snapshot(
        symbol, 'SHORT', candidate_bar_id=1,
        code='KC_LIVE_BODY_BREAKOUT_SHORT', authorized_entry=authorized,
    ))
    assert first is None
    assert engine.account.failed_breakout_revalidation[symbol]['bar_id'] == 1

    later = asyncio.run(engine._fresh_channel_entry_snapshot(
        symbol, 'SHORT', candidate_bar_id=2,
        code='KC_LIVE_BODY_BREAKOUT_SHORT', authorized_entry=authorized,
    ))
    assert later is None
    assert engine._entry_gate_diagnostics[
        (symbol, 'SHORT', 'ENTRY_REVALIDATION')
    ][1] == 'BLOCKED_FAILED_BREAKOUT_NO_LATE_CHASE'










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








