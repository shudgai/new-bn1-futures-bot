import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.entry_contract import (
    REALTIME_RAIL_BREACH_SHORT_CODE,
    evaluate_entry_contract,
)
from core.services.entry_firewall import validate_account_entry


def bearish_breach_frame():
    start = int(time.time() // 60) * 60_000 - 3 * 60_000
    rows = []
    for index in range(3):
        rows.append(dict(
            timestamp=start + index * 60_000,
            open=100.0, high=100.1, low=99.4, close=99.6,
            kc_upper=101.0, kc_middle=100.4 - index * 0.05,
            kc_lower=99.1, atr=1.0,
            ma3=99.4, ma5=99.5, ma15=99.8,
            is_closed=True,
        ))
    rows.append(dict(
        timestamp=start + 3 * 60_000,
        open=99.8, high=100.0, low=98.7, close=98.8,
        kc_upper=101.0, kc_middle=100.25, kc_lower=99.1,
        atr=1.0, ma3=99.0, ma5=99.2, ma15=99.6,
        is_closed=False,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs.update(timeframe_ms=60_000, entry_finality_verified=True)
    return frame


def test_realtime_short_breach_enters_without_closed_bar_pair():
    frame = bearish_breach_frame()

    decision = evaluate_entry_contract(
        frame, 98.8, symbol='龙虾/USDT',
    )

    assert decision is not None
    assert decision['side'] == 'SHORT'
    assert decision['type'] == REALTIME_RAIL_BREACH_SHORT_CODE
    assert decision['entry_phase'] == 'REALTIME_RAIL_BREACH'
    assert decision['realtime_body_atr'] == pytest.approx(1.0)


def test_realtime_short_survives_fresh_account_entry_revalidation():
    frame = bearish_breach_frame()
    decision = evaluate_entry_contract(frame, 98.8, symbol='龙虾/USDT')
    account = SimpleNamespace(
        positions={}, trades=[], last_closed_at={},
        entry_frame_provider=AsyncMock(return_value=frame),
    )

    validated = asyncio.run(validate_account_entry(
        account, '龙虾/USDT', 'SHORT', {
            'entry_signal_code': REALTIME_RAIL_BREACH_SHORT_CODE,
            'channel_confirmation_bar_id': decision['confirmation_bar_id'],
        },
    ))

    assert validated['type'] == REALTIME_RAIL_BREACH_SHORT_CODE
    assert validated['pending_signal_id'] == decision['pending_signal_id']


def test_realtime_short_is_rejected_at_1_5_atr_below_lower_rail():
    frame = bearish_breach_frame()
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, 97.6, symbol='龙虾/USDT', diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_BY_EXTENDED_BREAKOUT'


@pytest.mark.parametrize('wick_bar', ['live', 'previous'])
def test_realtime_short_is_rejected_for_lower_shadow_over_half_range(wick_bar):
    frame = bearish_breach_frame()
    if wick_bar == 'live':
        frame.loc[frame.index[-1], 'low'] = 97.5
    else:
        frame.loc[frame.index[-2], 'low'] = 98.0
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, 98.8, symbol='龙虾/USDT', diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_BY_BOTTOM_SHADOW'


@pytest.mark.parametrize('failure,reason', [
    ('small_body', 'WAIT_REALTIME_SHORT_BODY_BELOW_0_3_ATR'),
    ('kc_flat', 'BLOCKED_REALTIME_SHORT_KC_NOT_FALLING'),
    ('ma_reversed', 'BLOCKED_REALTIME_SHORT_MA_ORDER'),
])
def test_realtime_short_fails_closed_when_trigger_conditions_are_missing(failure, reason):
    frame = bearish_breach_frame()
    if failure == 'small_body':
        frame.loc[frame.index[-1], 'open'] = 99.0
    elif failure == 'kc_flat':
        frame.loc[frame.index[-1], 'kc_middle'] = frame.loc[frame.index[-2], 'kc_middle']
    else:
        frame.loc[frame.index[-1], 'ma3'] = 99.4
        frame.loc[frame.index[-1], 'ma5'] = 99.2
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, 98.8, symbol='龙虾/USDT', diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics['reason'] == reason


def test_realtime_route_code_cannot_be_reused_after_quote_returns_inside_rail():
    frame = bearish_breach_frame()
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, 99.1, code=REALTIME_RAIL_BREACH_SHORT_CODE,
        symbol='龙虾/USDT', diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_REALTIME_SHORT_RAIL_NOT_BREACHED'
