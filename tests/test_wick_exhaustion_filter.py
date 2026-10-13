import pandas as pd
import pytest

from core.services.entry_contract import entry_candle_shape_problem
from core.services.strategies.unified_entry_strategy import (
    UnifiedEntryStrategy,
    wick_exhaustion_problem,
)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_low_body_to_range_is_rejected_for_both_sides(side):
    candle = dict(open=100.0, close=100.2, high=100.9, low=99.1)
    assert wick_exhaustion_problem(candle, side) == 'REJECT_ENTRY: DOMINATED_BY_WICKS'


def test_long_upper_wick_resistance_is_rejected():
    candle = dict(open=100.0, close=101.0, high=101.71, low=99.5)
    assert wick_exhaustion_problem(candle, 'LONG') == 'REJECT_ENTRY: UPPER_WICK_RESISTANCE'


def test_short_lower_wick_support_is_rejected():
    candle = dict(open=101.0, close=100.0, high=101.5, low=99.29)
    assert wick_exhaustion_problem(candle, 'SHORT') == 'REJECT_ENTRY: LOWER_WICK_SUPPORT'


@pytest.mark.parametrize('side,candle', [
    ('LONG', dict(open=100.0, close=101.0, high=101.2, low=98.8)),
    ('SHORT', dict(open=101.0, close=100.0, high=102.2, low=99.8)),
])
def test_non_resisting_opposite_side_wick_does_not_block(side, candle):
    assert wick_exhaustion_problem(candle, side) is None


@pytest.mark.parametrize('side,candle', [
    ('LONG', dict(open=100.0, close=101.0, high=101.7, low=99.5)),
    ('SHORT', dict(open=101.0, close=100.0, high=101.5, low=99.3)),
])
def test_opposing_wick_exactly_at_70_percent_is_allowed(side, candle):
    assert wick_exhaustion_problem(candle, side) is None


def _shape_frame(candle):
    row = dict(timestamp=60_000, **candle, is_closed=True)
    return pd.DataFrame([row])


@pytest.mark.parametrize('side,candle,expected', [
    ('LONG', dict(open=100.0, close=100.2, high=100.9, low=99.1),
     'REJECT_ENTRY: DOMINATED_BY_WICKS'),
    ('LONG', dict(open=100.0, close=101.0, high=101.71, low=99.5),
     'REJECT_ENTRY: UPPER_WICK_RESISTANCE'),
    ('SHORT', dict(open=101.0, close=100.0, high=101.5, low=99.29),
     'REJECT_ENTRY: LOWER_WICK_SUPPORT'),
])
def test_entry_contract_rejects_wick_exhaustion_on_authorizing_candle(side, candle, expected):
    frame = _shape_frame(candle)
    decision = {
        'side': side,
        'entry_phase': 'KC_2BAR_CLOSED_CONFIRM',
        'breakout_bar_id': 60_000,
        'pair_confirmation_bar_id': 60_000,
    }
    assert entry_candle_shape_problem(frame, decision=decision) == expected


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_unified_strategy_rejects_live_wick_exhaustion_before_pipeline(side):
    if side == 'LONG':
        candle = dict(open=100.0, close=100.2, high=100.9, low=99.1)
        ma5, ma15 = 100.1, 100.0
    else:
        candle = dict(open=100.2, close=100.0, high=100.9, low=99.1)
        ma5, ma15 = 100.1, 100.2
    frame = pd.DataFrame([dict(
        timestamp=60_000, **candle, is_closed=False,
        ma5=ma5, ma15=ma15, ma3=ma5,
    )])
    allowed, reason, _ = UnifiedEntryStrategy().evaluate_entry(
        frame, candle['close'], side,
    )
    assert not allowed
    assert reason == 'REJECT_ENTRY: DOMINATED_BY_WICKS'
