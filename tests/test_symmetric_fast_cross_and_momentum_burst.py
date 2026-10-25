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
            'ma3': 99.8 if side == 'LONG' else 100.2,
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
        'ma3': live_ma5,
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


def test_ma_tangling_exemption_for_fast_cross():
    """Verify section 3.1: Exemption for BLOCKED_BY_MA_TANGLING on valid fast cross."""
    pipeline = EntryGatePipeline()

    # Gap between MA5 and MA15 is 0.05 ATR (< 0.25 ATR), which normally triggers tangling
    # With signal_code=None:
    long_frame, long_quote = make_test_frame_for_cross('LONG', body_atr=0.5, space_ratio=0.35, tangling_gap=0.05)
    assert is_ma_tangling(long_frame, long_quote, signal_code=None) is True
    assert pipeline.chop_lockout_problem(long_frame, long_quote, signal_code=None, allow_flat_lines=True) == 'BLOCKED_BY_MA_TANGLING'

    # With signal_code=GOLDEN_CROSS_FAST_LONG_CODE and valid momentum:
    assert is_ma_tangling(long_frame, long_quote, signal_code=GOLDEN_CROSS_FAST_LONG_CODE) is False
    assert pipeline.chop_lockout_problem(long_frame, long_quote, signal_code=GOLDEN_CROSS_FAST_LONG_CODE, allow_flat_lines=True) is None

    # Symmetrically for SHORT:
    short_frame, short_quote = make_test_frame_for_cross('SHORT', body_atr=0.5, space_ratio=0.35, tangling_gap=0.05)
    assert is_ma_tangling(short_frame, short_quote, signal_code=None) is True
    assert pipeline.chop_lockout_problem(short_frame, short_quote, signal_code=None, allow_flat_lines=True) == 'BLOCKED_BY_MA_TANGLING'

    assert is_ma_tangling(short_frame, short_quote, signal_code=DEATH_CROSS_FAST_SHORT_CODE) is False
    assert pipeline.chop_lockout_problem(short_frame, short_quote, signal_code=DEATH_CROSS_FAST_SHORT_CODE, allow_flat_lines=True) is None


def test_kc_room_gate_and_breakout_strategy_exemption():
    """Verify section 3.2: KC room 25% gate and STRATEGY_TYPE_BREAKOUT exemption."""
    frame, _ = make_test_frame_for_cross('LONG')
    # Upper = 104, Lower = 96, Width = 8

    # Inside channel with 30% space (quote = 101.6) -> Passes
    assert strict_kc_entry_problem(frame, 101.6, 'LONG') is None
    # Inside channel with 10% space (quote = 103.2) -> Fails with INSUFFICIENT_UPPER_SPACE
    assert strict_kc_entry_problem(frame, 103.2, 'LONG') == 'INSUFFICIENT_UPPER_SPACE'

    # Symmetrically for SHORT:
    # Inside channel with 30% space (quote = 98.4) -> Passes
    assert strict_kc_entry_problem(frame, 98.4, 'SHORT') is None
    # Inside channel with 10% space (quote = 96.8) -> Fails with INSUFFICIENT_LOWER_SPACE
    assert strict_kc_entry_problem(frame, 96.8, 'SHORT') == 'INSUFFICIENT_LOWER_SPACE'

    # Outside outer rail (breakout):
    # LONG breakout at 104.5 (> upper 104.0):
    # Without breakout strategy_type: blocked
    assert strict_kc_entry_problem(frame, 104.5, 'LONG') == 'INSUFFICIENT_UPPER_SPACE'
    # Long breakout remains exempt from the directional room check.
    assert strict_kc_entry_problem(frame, 104.5, 'LONG', strategy_type=STRATEGY_TYPE_BREAKOUT) is None

    # A short breakout may not chase below the lower rail.
    assert strict_kc_entry_problem(frame, 95.5, 'SHORT') == 'INSUFFICIENT_LOWER_SPACE'
    assert strict_kc_entry_problem(
        frame, 95.5, 'SHORT', strategy_type=STRATEGY_TYPE_BREAKOUT,
    ) == 'INSUFFICIENT_LOWER_SPACE'


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_fast_cross_requires_directional_ma15_slope(side):
    frame, quote = make_test_frame_for_cross(side)
    # The current cross policy permits flat MA15, but rejects movement against
    # the cross direction.
    frame.loc[frame.index[-1], "ma15"] = frame.loc[frame.index[-2], "ma15"]
    assert fast_cross_evidence(frame, quote, side) is not None

    opposite = -0.05 if side == "LONG" else 0.05
    frame.loc[frame.index[-1], "ma15"] = (
        float(frame.iloc[-2]["ma15"]) + opposite
    )
    assert fast_cross_evidence(frame, quote, side) is None


@pytest.mark.parametrize(
    ("quote", "opening", "expected"),
    [
        (98.0, 98.5, None),              # exactly 25% room, normal body
        (97.59, 98.09, "INSUFFICIENT_LOWER_SPACE"),
        (97.2, 98.0, None),              # exactly 15% room, high momentum
        (97.19, 98.0, "INSUFFICIENT_LOWER_SPACE"),
        (95.5, 96.5, "INSUFFICIENT_LOWER_SPACE"),  # outside lower rail
    ],
)
def test_short_kc_room_floor_cannot_be_bypassed_by_breakout(
    quote, opening, expected,
):
    frame, _ = make_test_frame_for_cross("SHORT")
    frame.loc[frame.index[-1], "open"] = opening
    result = strict_kc_entry_problem(
        frame, quote, "SHORT", strategy_type=STRATEGY_TYPE_BREAKOUT,
    )
    assert result == expected


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
