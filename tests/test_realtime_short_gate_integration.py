import pandas as pd

from core.gates.pipeline import (
    STRATEGY_TYPE_BREAKOUT,
    entry_market_structure_problem,
    entry_strategy_type,
    is_live_short_lower_rail_breakout,
    strict_kc_entry_problem,
)


def qualifying_live_short_frame():
    rows = []
    for index in range(15):
        rows.append({
            'timestamp': 1_800_000 + index * 60_000,
            'open': 100.0,
            'high': 100.1,
            'low': 99.9,
            'close': 100.0,
            'atr': 1.0,
            'kc_upper': 100.2,
            'kc_middle': 100.0,
            'kc_lower': 99.8,
            'ma3': 100.0,
            'ma5': 100.0,
            'ma15': 100.1,
            'is_closed': True,
        })
    rows[-3]['ma5'] = 100.2
    rows[-2]['ma5'] = 100.1
    rows[-1].update(
        open=100.0, high=100.1, low=99.4, close=99.9,
        ma3=100.0, ma5=100.0, ma15=100.1, is_closed=False,
    )
    frame = pd.DataFrame(rows)
    frame.attrs.update(timeframe_ms=60_000, entry_finality_verified=True)
    return frame


def test_realtime_lower_rail_entry_is_classified_as_breakout():
    assert entry_strategy_type('REALTIME_RAIL_BREACH') == STRATEGY_TYPE_BREAKOUT


def test_qualified_live_short_break_bypasses_squeeze_and_midline_checks():
    frame = qualifying_live_short_frame()
    quote = 99.5  # 0.3 ATR below the narrow KC lower rail.

    assert is_live_short_lower_rail_breakout(frame, quote)
    assert strict_kc_entry_problem(
        frame, quote, 'SHORT', strategy_type=STRATEGY_TYPE_BREAKOUT,
    ) is None
    assert entry_market_structure_problem(
        frame, quote, 'SHORT', entry_phase='REALTIME_RAIL_BREACH',
    ) is None


def test_live_short_exception_fails_closed_inside_rail_or_on_rising_ma5():
    frame = qualifying_live_short_frame()
    assert not is_live_short_lower_rail_breakout(frame, 99.7)

    frame.iloc[-1, frame.columns.get_loc('ma5')] = 100.3
    assert not is_live_short_lower_rail_breakout(frame, 99.5)
