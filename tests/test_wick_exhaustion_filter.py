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




