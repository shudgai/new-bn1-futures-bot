import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.exits.peak_valley_exit import PeakValleyExit
from core.gates.holding_protection_gate import (
    closed_peak_valley_reversal_evidence,
    intrabar_peak_valley_reversal_evidence,
    HoldingProtectionExitGate,
)


def frame_for(side, *, closed=False, quote=107.0):
    if side == 'LONG':
        rows = [
            dict(open=108.0, high=109.0, low=107.0, close=108.5,
                 ma3=108.2, ma5=108.3, ma15=108.0, atr=1.0,
                 kc_upper=111.0, kc_middle=109.0, kc_lower=107.0,
                 timestamp=60_000, is_closed=True),
            dict(open=109.0, high=110.0, low=108.0, close=109.5,
                 ma3=109.1, ma5=109.0, ma15=108.5, atr=1.0,
                 kc_upper=112.0, kc_middle=110.0, kc_lower=108.0,
                 timestamp=120_000, is_closed=True),
            dict(open=109.0, high=110.0, low=106.8, close=quote,
                 ma3=109.0, ma5=108.0, ma15=108.5, atr=1.0,
                 kc_upper=112.0, kc_middle=110.0, kc_lower=108.0,
                 timestamp=180_000, is_closed=closed),
        ]
    else:
        rows = [
            dict(open=92.0, high=93.0, low=91.0, close=91.5,
                 ma3=91.8, ma5=91.7, ma15=92.0, atr=1.0,
                 kc_upper=93.0, kc_middle=91.0, kc_lower=89.0,
                 timestamp=60_000, is_closed=True),
            dict(open=91.0, high=92.0, low=90.0, close=90.5,
                 ma3=90.9, ma5=91.0, ma15=91.5, atr=1.0,
                 kc_upper=92.0, kc_middle=90.0, kc_lower=88.0,
                 timestamp=120_000, is_closed=True),
            dict(open=91.0, high=93.2, low=90.0, close=quote,
                 ma3=91.0, ma5=92.0, ma15=91.5, atr=1.0,
                 kc_upper=92.0, kc_middle=90.0, kc_lower=88.0,
                 timestamp=180_000, is_closed=closed),
        ]
    data = pd.DataFrame(rows)
    data.attrs['timeframe_ms'] = 60_000
    return data


@pytest.mark.parametrize('side,quote,pivot', [
    ('LONG', 107.0, 110.0),
    ('SHORT', 93.0, 90.0),
])
def test_intrabar_confirmed_peak_or_valley_is_authorized_immediately(side, quote, pivot):
    position = {
        'side': side, 'entry_price': 100.0,
        'highest_price': 110.0, 'lowest_price': 90.0,
        'bars_held': 1, 'qty': 1.0,
    }
    frame = frame_for(side, quote=quote)
    details = intrabar_peak_valley_reversal_evidence(
        position, frame, side, quote, 1.0, pivot, 10.0,
    )

    assert details is not None
    candidate = (
        'EXIT_LONG_ON_FRACTAL_PEAK'
        if side == 'LONG' else 'EXIT_SHORT_ON_FRACTAL_VALLEY'
    )
    allowed, _, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, quote, candidate,
        {'intrabar_pivot_confirmation': True, 'atr': 1.0,
         'pivot_price': pivot, 'favorable_move': 10.0,
         'peak_roe': 0.1, 'valley_roe': 0.1, 'peak_gain_atr': 10.0},
    )
    assert allowed
    assert details['retrace_atr'] >= 0.35


@pytest.mark.parametrize('side,quote,pivot', [
    ('LONG', 109.2, 110.0),
    ('SHORT', 90.8, 90.0),
])
def test_recovery_before_structural_confirmation_keeps_position_open(side, quote, pivot):
    position = {'side': side, 'entry_price': 100.0}
    frame = frame_for(side, quote=quote)
    assert intrabar_peak_valley_reversal_evidence(
        position, frame, side, quote, 1.0, pivot, 10.0,
    ) is None


@pytest.mark.parametrize('side,quote,pivot', [
    ('LONG', 109.2, 110.0),
    ('SHORT', 90.8, 90.0),
])
def test_same_color_or_weak_intrabar_retrace_is_not_a_peak_valley_exit(side, quote, pivot):
    """只有出現足夠實體的反向 K 線才可盤中確認峰谷。"""
    position = {'side': side, 'entry_price': 100.0}
    frame = frame_for(side, quote=quote)

    assert intrabar_peak_valley_reversal_evidence(
        position, frame, side, quote, 1.0, pivot, 10.0,
    ) is None
    result, _ = PeakValleyExit.evaluate(position, frame, quote)
    assert result is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_closed_strong_reversal_is_confirmed_but_doji_and_weak_body_wait(side):
    strong = frame_for(side, closed=True, quote=0.0)
    if side == 'LONG':
        strong.loc[2, ['open', 'close', 'high', 'low', 'ma5']] = [110., 109.2, 110.2, 109.0, 109.5]
    else:
        strong.loc[2, ['open', 'close', 'high', 'low', 'ma5']] = [90., 90.8, 91.0, 89.8, 90.5]
    assert closed_peak_valley_reversal_evidence(strong, side, 1.0) is not None

    weak = strong.copy()
    if side == 'LONG':
        weak.loc[2, ['open', 'close', 'high', 'low', 'ma5']] = [110., 109.8, 110.1, 109.7, 109.9]
    else:
        weak.loc[2, ['open', 'close', 'high', 'low', 'ma5']] = [90., 90.2, 90.3, 89.9, 90.1]
    assert closed_peak_valley_reversal_evidence(weak, side, 1.0) is None

    doji = strong.copy()
    if side == 'LONG':
        doji.loc[2, ['open', 'close', 'high', 'low', 'ma5']] = [110., 110., 110.2, 109.8, 110.1]
    else:
        doji.loc[2, ['open', 'close', 'high', 'low', 'ma5']] = [90., 90., 90.2, 89.8, 89.9]
    assert closed_peak_valley_reversal_evidence(doji, side, 1.0) is None


@pytest.mark.parametrize('side,quote,expected', [
    ('LONG', 107.0, 'EXIT_LONG_ON_FRACTAL_PEAK'),
    ('SHORT', 93.0, 'EXIT_SHORT_ON_FRACTAL_VALLEY'),
])
def test_confirmed_live_peak_valley_precedes_generic_pressure_reason(side, quote, expected):
    position = {
        'side': side, 'entry_price': 100.0,
        'highest_price': 110.0, 'lowest_price': 90.0,
    }
    result, details = PeakValleyExit.evaluate(position, frame_for(side, quote=quote), quote)
    assert result == expected
    assert details['intrabar_pivot_confirmation'] is True


def test_confirmed_live_peak_closes_at_current_quote_without_waiting_for_next_bar(monkeypatch):
    from core.services.exits import realtime_profit_exit

    async def scenario():
        frame = frame_for('LONG', quote=107.0)
        frame.loc[2, 'kc_middle'] = 100.0
        position = {
            'symbol': 'X', 'side': 'LONG', 'entry_price': 100.0,
            'highest_price': 110.0, 'lowest_price': 100.0,
            'qty': 1.0, 'margin': 1000.0, 'leverage': 1.0,
            'open_timestamp': time.time() - 120,
            'entry_mode': 'CHANNEL_SWING', 'bars_held': 3,
        }
        account = SimpleNamespace(
            positions={'X': position}, position_meta={},
            save_state=Mock(), log=Mock(),
            close_position=AsyncMock(return_value=True),
        )
        engine = SimpleNamespace(
            is_running=True, account=account,
            _channel_exit_frames={'X': frame},
        )
        snapshot = {
            'reason': None, 'current_bar_is_closed': False,
            'live_open': 109.0, 'live_high': 110.0, 'live_low': 106.8,
            'live_bar_ms': 180_000.0, 'closed_bar_ms': 120_000.0,
            'live_kc_upper': 112.0, 'live_kc_middle': 100.0,
            'live_kc_lower': 88.0, 'atr': 1.0,
            'history_5': [],
        }
        monkeypatch.setattr(
            realtime_profit_exit, 'enforce_hard_stop',
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            realtime_profit_exit, 'cached_tick_indicators',
            lambda *_args: (snapshot, 1.0),
        )
        monkeypatch.setattr(
            'core.services.exits.dual_track_exit_service.detect_climax_reversal',
            lambda *_args: None,
        )

        assert await realtime_profit_exit.enforce_realtime_profit_exit(
            engine, 'X', 107.0, time.time() * 1000,
        )
        account.close_position.assert_awaited_once_with(
            'X', 107.0, 'Channel Swing EXIT_BY_VERIFIED_FRACTAL_PEAK',
            is_manual=True,
        )

    asyncio.run(scenario())
