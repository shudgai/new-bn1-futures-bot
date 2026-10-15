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


def test_wild_symbol_realtime_breach_is_blocked_by_entry_contract():
    frame = bearish_breach_frame()
    diagnostics = {}
    decision = evaluate_entry_contract(
        frame, 98.8, symbol='龙虾/USDT', diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_WILD_SWING_REQUIRES_CLOSED_1M_BAR'


def test_non_meme_realtime_short_helper_remains_available():
    frame = bearish_breach_frame()
    with patch('core.services.entry_room_service.entry_room', return_value={'target': 95.0}):
        candidate = evaluate_realtime_short_rail_breach(
            frame, 98.8, symbol='TEST/USDT',
        )

    assert candidate['action'] == 'ENTER'
    assert candidate['side'] == 'SHORT'
    assert candidate['type'] == REALTIME_RAIL_BREACH_SHORT_CODE


def test_realtime_short_requires_net_reward_at_least_net_risk():
    frame = bearish_breach_frame()
    diagnostics = {}
    with patch('core.services.entry_room_service.entry_room', return_value={'target': 98.0}), \
            patch('core.services.entry_contract.check_entry_gates', return_value=(True, None)):
        decision = evaluate_entry_contract(
            frame, 98.8, symbol='龍蝦/USDT', diagnostics=diagnostics,
        )

    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_WILD_SWING_REQUIRES_CLOSED_1M_BAR'


def test_realtime_short_fails_closed_without_confirmed_profit_target():
    frame = bearish_breach_frame()
    diagnostics = {}
    with patch('core.services.entry_room_service.entry_room', return_value={'allowed': False}), \
            patch('core.services.entry_contract.check_entry_gates', return_value=(True, None)):
        decision = evaluate_entry_contract(
            frame, 98.8, symbol='龍蝦/USDT', diagnostics=diagnostics,
        )

    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_WILD_SWING_REQUIRES_CLOSED_1M_BAR'


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
    assert diagnostics['reason'] == 'BLOCKED_WILD_SWING_REQUIRES_CLOSED_1M_BAR'


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
    assert diagnostics['reason'] == 'BLOCKED_WILD_SWING_REQUIRES_CLOSED_1M_BAR'


@pytest.mark.parametrize('failure', ['small_body', 'kc_flat', 'ma_reversed'])
def test_wild_symbol_live_route_fails_closed_before_legacy_trigger_checks(failure):
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
    assert diagnostics['reason'] == 'BLOCKED_WILD_SWING_REQUIRES_CLOSED_1M_BAR'


def test_realtime_route_code_cannot_be_reused_after_quote_returns_inside_rail():
    frame = bearish_breach_frame()
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, 99.1, code=REALTIME_RAIL_BREACH_SHORT_CODE,
        symbol='龙虾/USDT', diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics['reason'] == 'BLOCKED_WILD_SWING_REQUIRES_CLOSED_1M_BAR'
