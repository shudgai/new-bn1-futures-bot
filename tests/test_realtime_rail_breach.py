import time
from unittest.mock import patch

import pandas as pd
import pytest

from core.services.entry_contract import (
    REALTIME_RAIL_BREACH_SHORT_CODE,
    evaluate_entry_contract,
    evaluate_realtime_short_rail_breach,
    realtime_short_reward_risk,
)


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

    with patch('core.services.entry_room_service.entry_room', return_value={'target': 95.0}):
        decision = evaluate_realtime_short_rail_breach(frame, 98.8, symbol='龙虾/USDT')

    assert decision['action'] == 'ENTER'
    assert decision['side'] == 'SHORT'
    assert decision['type'] == REALTIME_RAIL_BREACH_SHORT_CODE
    assert decision['entry_phase'] == 'REALTIME_RAIL_BREACH'
    assert decision['realtime_body_atr'] == pytest.approx(1.0)


def test_meme_realtime_short_rejects_lower_wick_resistance():
    frame = bearish_breach_frame()
    frame.loc[frame.index[-1], ['ma5', 'ma15']] = [99.4, 99.8]
    frame.loc[frame.index[-1], ['low', 'kc_lower']] = [99.0, 99.5]
    with patch('core.services.entry_room_service.entry_room', return_value={'target': 97.5}):
        candidate = evaluate_realtime_short_rail_breach(
            frame, 99.4, symbol='CAP/USDT',
        )

    assert candidate['action'] == 'WAIT'
    assert candidate['reason'] == 'REJECT_ENTRY: LOWER_WICK_SUPPORT'


def test_realtime_short_survives_fresh_account_entry_revalidation():
    frame = bearish_breach_frame()
    with patch('core.services.entry_room_service.entry_room', return_value={'target': 95.0}):
        candidate = evaluate_realtime_short_rail_breach(frame, 98.8, symbol='龙虾/USDT')

    assert candidate['action'] == 'ENTER'
    assert candidate['expected_reward_risk'] >= 1.0
    assert candidate['structural_target'] == 95.0


def test_realtime_short_requires_net_reward_at_least_net_risk():
    frame = bearish_breach_frame()
    diagnostics = {}
    with patch('core.services.entry_room_service.entry_room', return_value={'target': 98.0}), \
            patch('core.services.entry_contract.check_entry_gates', return_value=(True, None)):
        decision = evaluate_entry_contract(
            frame, 98.8, symbol='龙蝦/USDT', diagnostics=diagnostics,
        )

    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_REALTIME_SHORT_REWARD_BELOW_RISK'


def test_realtime_short_fails_closed_without_confirmed_profit_target():
    frame = bearish_breach_frame()
    diagnostics = {}
    with patch('core.services.entry_room_service.entry_room', return_value={'allowed': False}), \
            patch('core.services.entry_contract.check_entry_gates', return_value=(True, None)):
        decision = evaluate_entry_contract(
            frame, 98.8, symbol='龍蝦/USDT', diagnostics=diagnostics,
        )

    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_REALTIME_SHORT_REWARD_RISK_UNAVAILABLE'


def test_realtime_short_net_reward_risk_uses_structural_target_and_1_5_atr_stop():
    frame = bearish_breach_frame()
    with patch('core.services.entry_room_service.entry_room', return_value={'target': 96.0}):
        estimate = realtime_short_reward_risk(frame, 98.8, 1.0)

    assert estimate['target'] == 96.0
    assert estimate['stop'] == pytest.approx(100.3)
    assert estimate['reward'] > estimate['risk']
    assert estimate['reward_risk'] > 1.0


def test_realtime_short_is_rejected_at_1_5_atr_below_lower_rail():
    frame = bearish_breach_frame()
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, 95.8, symbol='龙虾/USDT', diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_BY_EXTENDED_BREAKOUT'


@pytest.mark.parametrize('wick_bar', ['live', 'previous'])
def test_realtime_short_is_rejected_for_extreme_lower_shadow(wick_bar):
    frame = bearish_breach_frame()
    if wick_bar == 'live':
        frame.loc[frame.index[-1], 'low'] = 95.8
    else:
        frame.loc[frame.index[-2], 'low'] = 98.0
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, 98.8, symbol='龙虾/USDT', diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics['reason'] == (
        'REJECT_ENTRY: LOWER_WICK_SUPPORT'
        if wick_bar == 'live' else 'REJECT_ENTRY: DOMINATED_BY_WICKS'
    )


@pytest.mark.parametrize('failure,reason', [
    ('small_body', 'REJECT_ENTRY: DOMINATED_BY_WICKS'),
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
    assert diagnostics['reason'] == 'BLOCKED_REALTIME_SHORT_BREACH_NOT_QUALIFIED'
