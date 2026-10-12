import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.gates.holding_protection_gate import (
    INTRABAR_SPIKE_REBOUND_EXIT,
    HoldingProtectionExitGate,
)


def spike_frame(*, side, bar_id, high, low):
    previous_close = 100.0
    upper = 105.0
    lower = 95.0
    rows = [
        dict(
            timestamp=bar_id - 60_000, is_closed=True,
            open=previous_close, high=previous_close + 0.2,
            low=previous_close - 0.2, close=previous_close,
            atr=1.0, kc_upper=upper, kc_middle=previous_close,
            kc_lower=lower, ma5=previous_close, ma15=previous_close,
        ),
        dict(
            timestamp=bar_id, is_closed=False, open=100.0,
            high=high, low=low, close=100.0, atr=1.0,
            kc_upper=upper, kc_middle=previous_close, kc_lower=lower,
            ma5=previous_close, ma15=previous_close,
        ),
    ]
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame


def open_position(side, bar_id):
    return {
        'side': side,
        'entry_price': 100.0,
        'qty': 1.0,
        'open_timestamp': (bar_id - 120_000) / 1000.0,
        'entry_mode': 'CHANNEL_SWING',
    }


@pytest.mark.parametrize(
    ('quote', 'expected_trigger'),
    [
        (96.1, 'SPIKE_REBOUND_RATIO'),
        (94.57, 'SPIKE_REBOUND_PERCENT'),
    ],
)
def test_short_intrabar_lower_rail_spike_closes_on_either_rebound_rule(
    quote, expected_trigger,
):
    bar_id = 1_800_000
    position = open_position('SHORT', bar_id)
    position.pop('entry_mode')
    frame = spike_frame(side='SHORT', bar_id=bar_id, high=100.0, low=94.0)

    reason, details = HoldingProtectionExitGate.evaluate(position, frame, quote)

    assert reason == INTRABAR_SPIKE_REBOUND_EXIT
    assert details['trigger'] == expected_trigger
    assert details['intrabar_confirmation'] is True
    assert details['curr_low'] == pytest.approx(94.0)
    assert not bool(frame.iloc[-1]['is_closed'])


def test_long_intrabar_upper_rail_retracement_closes_before_bar_close():
    bar_id = 1_800_000
    position = open_position('LONG', bar_id)
    position.pop('entry_mode')
    frame = spike_frame(side='LONG', bar_id=bar_id, high=106.0, low=100.0)

    reason, details = HoldingProtectionExitGate.evaluate(position, frame, 103.8)

    assert reason == INTRABAR_SPIKE_REBOUND_EXIT
    assert details['trigger'] == 'SPIKE_RETRACEMENT_RATIO'
    assert details['retracement_ratio'] > 0.35


@pytest.mark.parametrize(
    ('side', 'high', 'low', 'quote'),
    [
        ('SHORT', 100.0, 94.0, 94.564),
        ('LONG', 106.0, 100.0, 103.9),
    ],
)
def test_strict_long_35_percent_and_short_0_6_percent_boundaries_do_not_trigger(
    side, high, low, quote,
):
    bar_id = 1_800_000
    position = open_position(side, bar_id)
    frame = spike_frame(side=side, bar_id=bar_id, high=high, low=low)

    reason, _ = HoldingProtectionExitGate.evaluate(position, frame, quote)

    assert reason is None


@pytest.mark.parametrize(
    ('side', 'high', 'low', 'quote'),
    [
        ('SHORT', 100.0, 94.0, 94.2),
        ('SHORT', 100.0, 95.01, 96.8),
        ('LONG', 106.0, 100.0, 104.0),
        ('LONG', 104.99, 100.0, 103.2),
    ],
)
def test_spike_rebound_requires_rail_breach_and_rebound_threshold(
    side, high, low, quote,
):
    bar_id = 1_800_000
    position = open_position(side, bar_id)
    frame = spike_frame(side=side, bar_id=bar_id, high=high, low=low)

    reason, _ = HoldingProtectionExitGate.evaluate(position, frame, quote)

    assert reason is None


def test_same_bar_low_before_position_open_is_not_reconstructed_as_a_spike():
    bar_id = 1_800_000
    position = open_position('SHORT', bar_id)
    position['open_timestamp'] = (bar_id + 10_000) / 1000.0
    frame = spike_frame(side='SHORT', bar_id=bar_id, high=100.0, low=90.0)

    reason, _ = HoldingProtectionExitGate.evaluate(position, frame, 95.1)

    assert reason is None


def test_tracked_extremes_survive_position_refresh_in_metadata():
    bar_id = 1_800_000
    frame = spike_frame(side='SHORT', bar_id=bar_id, high=100.0, low=94.0)
    position = open_position('SHORT', bar_id)
    position.pop('entry_mode')
    reason, _ = HoldingProtectionExitGate.evaluate(position, frame, 94.1)
    assert reason is None

    from core.gates.holding_protection_gate import INTRABAR_SPIKE_STATE_KEY
    persisted = position[INTRABAR_SPIKE_STATE_KEY]
    refreshed_position = open_position('SHORT', bar_id)
    refreshed_position.pop('entry_mode')
    refreshed_position[INTRABAR_SPIKE_STATE_KEY] = persisted

    reason, details = HoldingProtectionExitGate.evaluate(
        refreshed_position, frame, 96.2,
    )

    assert reason == INTRABAR_SPIKE_REBOUND_EXIT
    assert details['curr_low'] == pytest.approx(94.0)


def test_intrabar_spike_exit_is_authorized_without_waiting_for_close():
    bar_id = 1_800_000
    position = open_position('SHORT', bar_id)
    position.pop('entry_mode')
    frame = spike_frame(side='SHORT', bar_id=bar_id, high=100.0, low=94.0)
    quote = 96.1
    reason, details = HoldingProtectionExitGate.evaluate(position, frame, quote)

    allowed, authorized_reason, verified = HoldingProtectionExitGate.validate_exit(
        position, frame, quote, reason, details,
    )

    assert allowed is True
    assert authorized_reason == INTRABAR_SPIKE_REBOUND_EXIT
    assert verified['intrabar_confirmation'] is True


def test_intrabar_spike_exit_is_blocked_for_channel_swing(monkeypatch):
    async def run():
        from core.services.exits import realtime_profit_exit
        from core.engine import TradingEngine

        now_ms = time.time() * 1000
        bar_id = int(now_ms // 60_000) * 60_000
        quote = 96.1
        position = open_position('SHORT', bar_id)
        position['open_timestamp'] = bar_id / 1000.0 - 120.0
        frame = spike_frame(side='SHORT', bar_id=bar_id, high=100.0, low=94.0)
        account = SimpleNamespace(
            positions={'X': position}, position_meta={}, save_state=Mock(),
            close_position=AsyncMock(return_value=True), log=Mock(),
        )
        engine = object.__new__(TradingEngine)
        engine.account = account
        engine.is_running = True
        engine._channel_exit_frames = {'X': frame}

        monkeypatch.setattr(
            realtime_profit_exit, 'enforce_hard_stop',
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            realtime_profit_exit, 'cached_tick_indicators',
            lambda *_: ({'current_bar_is_closed': False}, 1.0),
        )

        closed = await realtime_profit_exit.enforce_realtime_profit_exit(
            engine, 'X', quote, now_ms,
        )

        assert closed is False
        account.save_state.assert_called_once()
        assert 'channel_peak_profit_exit_state' in account.position_meta['X']
        assert 'intrabar_spike_rebound_state' not in account.position_meta['X']
        account.close_position.assert_not_awaited()

    asyncio.run(run())
