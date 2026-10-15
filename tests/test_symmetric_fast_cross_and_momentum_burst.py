import math
import pandas as pd
import pytest
from core.gates.pipeline import (
    GOLDEN_CROSS_FAST_LONG_CODE,
    DEATH_CROSS_FAST_SHORT_CODE,
    MOMENTUM_BURST_LONG_CODE,
    MOMENTUM_BURST_SHORT_CODE,
    STRATEGY_TYPE_BREAKOUT,
    EntryGatePipeline,
    fast_cross_evidence,
    is_ma_tangling,
    strict_kc_entry_problem,
    entry_market_structure_problem,
)
from core.services import entry_contract


def make_test_frame_for_cross(side: str, *, body_atr: float = 0.5, space_ratio: float = 0.35, tangling_gap: float = 0.05):
    """
    Generate candles where MA5 and MA15 cross on the live candle with tangling (close proximity).
    Upper = 104, Middle = 100, Lower = 96 (width = 8).
    ATR = 1.0.
    """
    rows = []
    base_time = 1_700_000_000_000
    for i in range(12):
        rows.append({
            'timestamp': base_time + i * 60_000,
            'open': 100.0,
            'high': 100.5,
            'low': 99.5,
            'close': 100.0,
            'atr': 1.0,
            'kc_upper': 104.0,
            'kc_middle': 100.0,
            'kc_lower': 96.0,
            'ma5': 99.8 if side == 'LONG' else 100.2,
            'ma15': 100.0,
            'kc_upper_slope': 0.1,
            'kc_lower_slope': -0.1,
            'channel_state': 'UP' if side == 'LONG' else 'DOWN',
            'is_closed': True,
        })

    # Live candle
    # width = 8.0. For LONG, upper = 104. remaining = (104 - quote) / 8.0.
    # To get remaining >= 25%, quote <= 102.0.
    # If space_ratio = 0.35, quote = 104 - 0.35 * 8 = 101.2 (> middle=100).
    # If side == 'SHORT', lower = 96. remaining = (quote - 96) / 8.0.
    # If space_ratio = 0.35, quote = 96 + 0.35 * 8 = 98.8 (< middle=100).
    if side == 'LONG':
        live_quote = 104.0 - space_ratio * 8.0
        live_open = live_quote - body_atr * 1.0
        live_high = live_quote + 0.1
        live_low = live_open - 0.1
        # Previous ma5 <= previous ma15 (99.8 <= 100.0)
        # We set live ma5 and ma15 so that live_ma5 > live_ma15 by tangling_gap
        # Note: live_ma5 is calculated as live['ma5'] + (quote - live['close']) / 5.0
        live_close = live_quote
        live_ma15 = 100.05
        live_ma5 = live_ma15 + tangling_gap
    else:
        live_quote = 96.0 + space_ratio * 8.0
        live_open = live_quote + body_atr * 1.0
        live_high = live_open + 0.1
        live_low = live_quote - 0.1
        # Previous ma5 >= previous ma15 (100.2 >= 100.0)
        live_close = live_quote
        live_ma15 = 99.95
        live_ma5 = live_ma15 - tangling_gap

    rows.append({
        'timestamp': base_time + 12 * 60_000,
        'open': live_open,
        'high': live_high,
        'low': live_low,
        'close': live_close,
        'atr': 1.0,
        'kc_upper': 104.0,
        'kc_middle': 100.0,
        'kc_lower': 96.0,
        'ma5': live_ma5,
        'ma15': live_ma15,
        'kc_upper_slope': 0.1,
        'kc_lower_slope': -0.1,
        'channel_state': 'UP' if side == 'LONG' else 'DOWN',
        'is_closed': False,
    })

    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame, live_quote


def test_golden_cross_and_death_cross_fast_lane_symmetry():
    """Verify section 1: Fast Lane MA Cross is fully symmetric and meets all specs."""
    # 1. LONG Golden Cross
    long_frame, long_quote = make_test_frame_for_cross('LONG', body_atr=0.5, space_ratio=0.35)
    long_ev = fast_cross_evidence(long_frame, long_quote, 'LONG')
    assert long_ev is not None
    assert long_ev['quote'] > long_ev['middle']
    assert long_ev['directional_space_ratio'] >= 0.25
    assert long_ev['body_atr'] >= 0.4
    assert long_ev['live_ma5'] > long_ev['live_ma15']
    assert long_ev['live_ma5'] > long_ev['previous_ma5']

    long_decision = entry_contract.evaluate_golden_cross_fast_lane(long_frame, long_quote, 'TEST/USDT')
    assert long_decision is not None
    assert long_decision['type'] == GOLDEN_CROSS_FAST_LONG_CODE
    assert long_decision['side'] == 'LONG'
    assert long_decision['upper_rail_space_ratio'] >= 0.25

    # 2. SHORT Death Cross
    short_frame, short_quote = make_test_frame_for_cross('SHORT', body_atr=0.5, space_ratio=0.35)
    short_ev = fast_cross_evidence(short_frame, short_quote, 'SHORT')
    assert short_ev is not None
    assert short_ev['quote'] < short_ev['middle']
    assert short_ev['directional_space_ratio'] >= 0.25
    assert short_ev['body_atr'] >= 0.4
    assert short_ev['live_ma5'] < short_ev['live_ma15']
    assert short_ev['live_ma5'] < short_ev['previous_ma5']

    short_decision = entry_contract.evaluate_death_cross_fast_lane(short_frame, short_quote, 'TEST/USDT')
    assert short_decision is not None
    assert short_decision['type'] == DEATH_CROSS_FAST_SHORT_CODE
    assert short_decision['side'] == 'SHORT'
    assert short_decision['lower_rail_space_ratio'] >= 0.25






@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_fast_cross_requires_directional_ma15_slope(side):
    from core.gates.pipeline import FAST_CROSS_MIN_MA15_SLOPE_ATR

    frame, quote = make_test_frame_for_cross(side)
    # Flat MA15 movement is rejected despite a valid MA5/MA15 cross.
    frame.loc[frame.index[-1], "ma15"] = frame.loc[frame.index[-2], "ma15"]
    assert fast_cross_evidence(frame, quote, side) is None

    # The strict <0.03 ATR threshold leaves the exact boundary admissible.
    delta = FAST_CROSS_MIN_MA15_SLOPE_ATR * float(frame.iloc[-2]["atr"])
    frame.loc[frame.index[-1], "ma15"] = (
        float(frame.iloc[-2]["ma15"]) + (delta if side == "LONG" else -delta)
    )
    assert fast_cross_evidence(frame, quote, side) is not None




def test_momentum_burst_symmetry_and_market_structure():
    """Verify section 2: Momentum Burst Long and Short are mirrored and satisfy entry pipeline."""
    from tests.test_intrabar_midline_entry import make_momentum_burst_frame

    # 1. LONG Momentum Burst
    long_frame, long_quote = make_momentum_burst_frame('LONG')
    long_decision = EntryGatePipeline.detect_momentum_burst(long_frame, long_quote, side='LONG')
    assert long_decision is not None
    assert long_decision['type'] == MOMENTUM_BURST_LONG_CODE
    assert long_decision['side'] == 'LONG'
    assert long_decision['body_atr'] >= 0.5
    assert long_decision['body_ratio'] >= 0.6
    assert long_decision['upper_rail_space_ratio'] >= 0.25

    # 2. SHORT Momentum Burst
    short_frame, short_quote = make_momentum_burst_frame('SHORT')
    short_decision = EntryGatePipeline.detect_momentum_burst(short_frame, short_quote, side='SHORT')
    assert short_decision is not None
    assert short_decision['type'] == MOMENTUM_BURST_SHORT_CODE
    assert short_decision['side'] == 'SHORT'
    assert short_decision['body_atr'] >= 0.5
    assert short_decision['body_ratio'] >= 0.6
    assert short_decision['lower_rail_space_ratio'] >= 0.25
