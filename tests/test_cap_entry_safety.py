import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.gates.holding_protection_gate import top_engulfing_flip_evidence
from core.gates.pipeline import (
    EntryGatePipeline,
    long_large_bearish_candle_problem,
)
from core.services.entry_contract import (
    pre_flight_safety_check,
    same_bar_close_entry_problem,
)
from core.services.strategies.outer_strategy import kc_bearish_midline_turn


def _with_entry_history(frame, minimum_rows=10):
    missing = max(0, minimum_rows - len(frame))
    if not missing:
        return frame
    first = frame.iloc[0].to_dict()
    first_timestamp = float(first['timestamp'])
    rows = []
    for offset in range(missing, 0, -1):
        row = dict(first)
        row['timestamp'] = first_timestamp - offset * 60_000
        row['is_closed'] = True
        rows.append(row)
    result = pd.concat([pd.DataFrame(rows), frame], ignore_index=True)
    result.attrs.update(frame.attrs)
    return result


def _frame(*, channel_state='KC向下', close=102.0, middle=102.2):
    rows = [
        {
            'timestamp': 1_700_000_000_000,
            'open': 103.0, 'close': 104.8, 'high': 105.2, 'low': 102.8,
            'atr': 1.0, 'kc_middle': 102.4, 'kc_upper': 106.0,
            'kc_lower': 98.5, 'ma5': 103.5, 'ma15': 99.0,
            'is_closed': True, 'channel_state': channel_state,
        },
        {
            'timestamp': 1_700_000_060_000,
            'open': 105.0, 'close': close, 'high': 105.1, 'low': 101.8,
            'atr': 1.0, 'kc_middle': middle, 'kc_upper': 105.8,
            'kc_lower': 98.0, 'ma5': 103.0, 'ma15': 100.0,
            'is_closed': True, 'channel_state': channel_state,
        },
    ]
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return _with_entry_history(frame)


@pytest.mark.parametrize(
    ('channel_state', 'close', 'expected'),
    [
        ('KC向下', 102.0, True),
        ('KC向上', 102.0, False),
        ('KC向下', 102.3, False),
    ],
)
def test_top_flip_requires_down_cross_and_bearish_channel(
    channel_state, close, expected,
):
    frame = _frame(channel_state=channel_state, close=close)

    assert kc_bearish_midline_turn(frame, close) is expected
    if expected:
        assert top_engulfing_flip_evidence(frame)['flip_short_allowed'] is True
        assert EntryGatePipeline.detect_top_waterfall_flip_short(
            frame, close, side='SHORT',
        ) is not None
    else:
        evidence = top_engulfing_flip_evidence(frame)
        assert evidence is not None
        assert evidence['flip_short_allowed'] is False
        assert EntryGatePipeline.detect_top_waterfall_flip_short(
            frame, close, side='SHORT',
        ) is None






def test_same_side_entry_is_blocked_only_for_the_closed_candle():
    now_ms = 1_800_000_060_000
    frame = pd.DataFrame([{
        'timestamp': now_ms, 'open': 100.0, 'close': 100.0,
        'high': 101.0, 'low': 99.0,
    }])
    frame.attrs['timeframe_ms'] = 60_000
    bar_start = (now_ms // 60_000) * 60_000 / 1000
    account = SimpleNamespace(
        last_closed_at={'CAP/USDT': bar_start + 10},
        last_closed_side={'CAP/USDT': 'LONG'},
    )

    assert same_bar_close_entry_problem(
        account, 'CAP/USDT', frame, 'LONG',
    ) == 'BLOCKED_SAME_BAR_REENTRY'
    assert same_bar_close_entry_problem(
        account, 'CAP/USDT', frame, 'SHORT',
    ) is None

    account.last_closed_at['CAP/USDT'] = bar_start - 1
    assert same_bar_close_entry_problem(
        account, 'CAP/USDT', frame, 'LONG',
    ) is None


def test_pipeline_rejects_same_bar_long_before_signal_authorization():
    now_ms = 1_800_000_060_000
    frame = pd.DataFrame([
        {
            'timestamp': now_ms - 60_000, 'open': 99.0, 'close': 100.0,
            'high': 100.2, 'low': 98.8, 'atr': 1.0, 'kc_middle': 99.5,
            'kc_upper': 101.0, 'kc_lower': 98.0, 'ma5': 99.0, 'ma15': 98.5,
            'is_closed': True,
        },
        {
            'timestamp': now_ms, 'open': 100.0, 'close': 100.2,
            'high': 100.3, 'low': 99.8, 'atr': 1.0, 'kc_middle': 99.6,
            'kc_upper': 101.1, 'kc_lower': 98.1, 'ma5': 99.1, 'ma15': 98.6,
            'is_closed': False,
        },
    ])
    frame.attrs['timeframe_ms'] = 60_000
    frame = _with_entry_history(frame)
    frame.attrs['timeframe_ms'] = 60_000
    bar_start = (now_ms // 60_000) * 60_000 / 1000
    account = SimpleNamespace(
        last_closed_at={'CAP/USDT': bar_start + 10},
        last_closed_side={'CAP/USDT': 'LONG'},
        positions={},
    )
    diagnostics = {}
    authorized = EntryGatePipeline().authorize(
        {
            'type': 'AUTHORIZED_REALTIME_BREAKOUT',
            'side': 'LONG',
            'ma5_direction_aligned': True,
        },
        frame, 100.2, symbol='CAP/USDT', account=account,
        requested_side='LONG', diagnostics=diagnostics,
    )

    assert authorized is None
    assert diagnostics['reason'] == 'BLOCKED_SAME_BAR_REENTRY'




