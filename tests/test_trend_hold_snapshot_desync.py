import pytest
import math
from unittest.mock import MagicMock, patch
from core.services.exits.trend_hold_evaluator import evaluate_trend_hold
from core.services.exits.realtime_profit_exit import cached_tick_indicators
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing

def create_position(side='SHORT', entry_price=0.0629, qty=1000, initial_sl=0.065):
    return {
        'symbol': 'LOBSTER/USDT',
        'side': side,
        'open_timestamp': 1790906940.0,
        'entry_price': entry_price,
        'qty': qty,
        'entry_atr': 0.003,
        'initial_sl': initial_sl,
        'peak_trailing_state': {
            'policy': 'abnormal_body_only_v2',
            'identity': [side, 1790906940.0, entry_price, qty],
            'peak_price': 0.058,
            'peak_net_pnl': 5.0, # Enough to trigger 2U ladder
            'entry_margin': 100.0,
            'armed': False
        }
    }

def create_mock_frame(last_closed_ts, live_ts, is_closed=False):
    import pandas as pd
    data = []

    # prev closed (T-2)
    data.append({
        'timestamp': last_closed_ts - 60000.0,
        'open': 0.063, 'high': 0.0635, 'low': 0.062, 'close': 0.0625,
        'ma3': 0.063, 'ma15': 0.066, 'kc_middle': 0.065, 'atr': 0.003,
        'is_closed': True
    })

    # last closed (T-1)
    data.append({
        'timestamp': last_closed_ts,
        'open': 0.0625, 'high': 0.0628, 'low': 0.061, 'close': 0.0619,
        'ma3': 0.062, 'ma15': 0.065, 'kc_middle': 0.064, 'atr': 0.0035,
        'is_closed': True
    })

    # live (T) if live_ts exists
    if live_ts:
        data.append({
            'timestamp': live_ts,
            'open': 0.0619, 'high': 0.0621, 'low': 0.060, 'close': 0.0605,
            'ma3': 0.061, 'ma15': 0.064, 'kc_middle': 0.063, 'atr': 0.004,
            'is_closed': is_closed
        })

    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = 60000
    return df

def test_desync_soft_exit_blocked():
    """SHORT position, live tick=10:10:26, latest closed=10:09, current 10:10 missing/unclosed."""
    # 10:10:26
    stamp = 1790907026000.0
    bar = 1790907000000.0 # 10:10:00
    last_closed_ts = bar - 60000.0 # 10:09:00

    # Frame hasn't received 10:10 yet (or only has 10:09 as live)
    frame = create_mock_frame(last_closed_ts=last_closed_ts, live_ts=None)

    snapshot, atr = cached_tick_indicators(frame, 0.061, stamp)

    # Snapshot should have snapshot_bar_id = 10:09
    assert snapshot['snapshot_bar_id'] == last_closed_ts
    assert snapshot['reason'] is None

    pos = create_position()

    decision = evaluate_peak_trailing(pos, 0.061, snapshot, atr)

        # Should be BLOCKED
    assert decision is None

        # Check logs

def test_sync_released_soft_exit_allowed():
    """Full snapshot, TRUE RELEASE condition -> ALLOWED."""
    stamp = 1790907026000.0
    bar = 1790907000000.0
    last_closed_ts = bar - 60000.0

    # Frame is perfectly synced
    frame = create_mock_frame(last_closed_ts=last_closed_ts, live_ts=bar)

    # Change ma3 to simulate RELEASED condition (ma5 > ma15)
    frame.loc[frame['timestamp'] == last_closed_ts, 'ma3'] = 0.067

    snapshot, atr = cached_tick_indicators(frame, 0.061, stamp)
    assert snapshot['reason'] is None

    pos = create_position()

    decision = evaluate_peak_trailing(pos, 0.061, snapshot, atr)
    assert decision is not None
    assert decision['action'] == 'FULL_CLOSE'
    assert decision['reason'] == 'EXIT_REALTIME_PEAK_TRAILING'

def test_no_snapshot_unknown_blocks_soft_allows_hard():
    """No usable snapshot -> UNKNOWN -> Soft blocked, Hard allowed."""
    stamp = 1790907026000.0
    bar = 1790907000000.0
    last_closed_ts = bar - 600000.0 # 10 mins ago -> Stale

    frame = create_mock_frame(last_closed_ts=last_closed_ts, live_ts=None)
    snapshot, atr = cached_tick_indicators(frame, 0.061, stamp)
    assert snapshot['reason'] == 'STALE_SNAPSHOT'

    pos = create_position(initial_sl=0.060) # Price > 0.060 -> Hard stop triggered

    decision = evaluate_peak_trailing(pos, 0.061, snapshot, atr)

    # Hard stop allowed!
    assert decision is not None
    assert decision['reason'] == 'EXIT_INITIAL_ATR_HARD_STOP'

def test_no_flapping_same_minute():
    """100 ticks in same minute before closed candle arrives should yield same exact snapshot bar id."""
    bar = 1790907000000.0
    last_closed_ts = bar - 60000.0
    frame = create_mock_frame(last_closed_ts=last_closed_ts, live_ts=None)

    for ms in range(1790907000000, 1790907059000, 1000): # ticks in the minute
        snapshot, atr = cached_tick_indicators(frame, 0.061, ms)
        assert snapshot['snapshot_bar_id'] == last_closed_ts
        assert snapshot['reason'] is None

def test_freshness_boundaries():
    """age ≈ 60s, age=299999ms, age=300000ms, age=300001ms."""
    # Base setup: stamp = 1790907000000 (10:10:00.000)
    stamp = 1790907000000.0
    bar = stamp

    # 1. age = 60s (60000ms) -> last_ms = bar - 60000 (10:09) -> SYNCED
    last_closed_ts = bar - 60000.0
    frame = create_mock_frame(last_closed_ts=last_closed_ts, live_ts=None)
    snapshot, _ = cached_tick_indicators(frame, 0.061, stamp)
    assert snapshot['snapshot_age'] == 60000.0
    assert snapshot['reason'] is None
    assert snapshot['snapshot_source'] == 'CLOSED_BAR_SYNCED'
    assert snapshot['fallback_used'] is False

    # 2. age = 299999ms (almost 5 mins) -> FALLBACK valid
    last_closed_ts = stamp - 299999.0
    frame = create_mock_frame(last_closed_ts=last_closed_ts, live_ts=None)
    snapshot, _ = cached_tick_indicators(frame, 0.061, stamp)
    assert snapshot['snapshot_age'] == 299999.0
    assert snapshot['reason'] is None
    assert snapshot['snapshot_source'] == 'CLOSED_BAR_FALLBACK'
    assert snapshot['fallback_used'] is True

    # 3. age = 300000ms (exactly 5 mins) -> FALLBACK valid
    last_closed_ts = stamp - 300000.0
    frame = create_mock_frame(last_closed_ts=last_closed_ts, live_ts=None)
    snapshot, _ = cached_tick_indicators(frame, 0.061, stamp)
    assert snapshot['snapshot_age'] == 300000.0
    assert snapshot['reason'] is None
    assert snapshot['snapshot_source'] == 'CLOSED_BAR_FALLBACK'
    assert snapshot['fallback_used'] is True

    # 4. age = 300001ms -> STALE / UNKNOWN
    last_closed_ts = stamp - 300001.0
    frame = create_mock_frame(last_closed_ts=last_closed_ts, live_ts=None)
    snapshot, _ = cached_tick_indicators(frame, 0.061, stamp)
    assert snapshot['snapshot_age'] == 300001.0
    assert snapshot['reason'] == 'STALE_SNAPSHOT'
    assert 'snapshot_source' not in snapshot # Returned early
