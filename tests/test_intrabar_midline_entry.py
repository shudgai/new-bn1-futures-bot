import asyncio
import time

import pandas as pd
import pytest

from core.gates.pipeline import (
    EntryGatePipeline,
    MOMENTUM_BURST_LONG_CODE,
    MOMENTUM_BURST_SHORT_CODE,
    entry_market_structure_problem,
    ma5_ma15_cross_chop_problem,
    pipeline,
    strict_kc_entry_problem,
)
from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry


def make_frame(side, *, phase='launch', quote=None):
    rows = []
    for index in range(12):
        middle = 99.56 + index * 0.04
        rows.append(dict(
            timestamp=(index + 1) * 60_000,
            is_closed=True,
            open=middle - 0.1,
            high=middle + 0.2,
            low=middle - 0.3,
            close=middle - 0.05,
            atr=0.3,
            kc_lower=middle - 4.0,
            kc_middle=middle,
            kc_upper=middle + 4.0,
            ma5=99.4 + index * 0.05,
            ma15=99.3 + index * 0.045,
            volume=100.0,
        ))

    if side == 'SHORT':
        # Build a genuinely bearish MA history for the mirrored short setup.
        # The previous fixture supplied rising bullish MAs to both directions.
        for index, row in enumerate(rows):
            row['ma5'] = 100.04 - 0.01 * index
            row['ma15'] = 100.19 - 0.01 * index

    last = rows[-1]
    if side == 'LONG':
        last.update(open=99.95, high=100.2, low=99.9, close=99.95)
        live = dict(
            open=100.2 if phase == 'retest' else 99.95,
            high=100.29 if phase == 'retest' else 100.24,
            low=100.02 if phase == 'retest' else 99.9,
            close=100.1 if phase == 'retest' else 99.99,
            kc_middle=100.04,
            ma5=last['ma5'] + 0.02,
            ma15=last['ma15'] + 0.01,
        )
        selected_quote = 100.1
    else:
        last.update(open=100.05, high=100.2, low=99.8, close=100.05)
        live = dict(
            open=99.8 if phase == 'retest' else 100.05,
            high=99.99 if phase == 'retest' else 100.19,
            low=99.71 if phase == 'retest' else 99.76,
            close=99.9 if phase == 'retest' else 100.01,
            kc_middle=99.96,
            ma5=last['ma5'] - 0.02,
            ma15=last['ma15'] - 0.01,
        )
        selected_quote = 99.9

    if phase == 'retest':
        last.update(
            open=99.95 if side == 'LONG' else 100.05,
            high=100.2 if side == 'LONG' else 100.19,
            low=99.81 if side == 'LONG' else 99.8,
            close=100.1 if side == 'LONG' else 99.9,
            volume=150.0,
        )
        rows[-2].update(
            open=99.9 if side == 'LONG' else 100.1,
            high=100.2,
            low=99.8,
            close=99.95 if side == 'LONG' else 100.05,
        )

    live.update(
        timestamp=last['timestamp'] + 60_000,
        is_closed=False,
        atr=0.3,
        kc_lower=live['kc_middle'] - 4.0,
        kc_upper=live['kc_middle'] + 4.0,
        close=selected_quote,
        volume=150.0 if phase == 'launch' else 80.0,
    )
    if quote is not None:
        selected_quote = quote
        live['high'] = max(live['high'], quote)
        live['low'] = min(live['low'], quote)
        live['close'] = quote
    rows.append(live)

    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame, selected_quote


def make_bearish_ma_midline_frame(*, quote=100.05):
    rows = []
    for index in range(10):
        middle = 100.0 + index * 0.02
        rows.append(dict(
            timestamp=(index + 1) * 60_000,
            is_closed=True,
            open=middle + 0.10,
            high=middle + 0.30,
            low=middle - 0.20,
            close=middle + 0.05,
            atr=1.0,
            kc_lower=96.0,
            kc_middle=100.0,
            kc_upper=104.0,
            ma5=101.20 - index * 0.02,
            ma15=100.75 - index * 0.01,
            volume=100.0,
        ))
    rows.append(dict(
        timestamp=660_000,
        is_closed=False,
        open=101.0,
        high=101.1,
        low=min(99.95, quote),
        close=100.9,
        atr=1.0,
        kc_lower=96.0,
        kc_middle=100.0,
        kc_upper=104.0,
        ma5=101.0,
        ma15=100.7,
        volume=80.0,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame, quote


def make_momentum_burst_frame(side, *, quote=None):
    frame, short_quote = make_bearish_ma_midline_frame(
        quote=100.05 if quote is None and side == 'SHORT' else quote or 100.05,
    )
    if side == 'SHORT':
        return frame, short_quote
    source = frame.copy()
    for target, original in (
        ('open', 'open'),
        ('close', 'close'),
        ('high', 'low'),
        ('low', 'high'),
        ('kc_upper', 'kc_lower'),
        ('kc_lower', 'kc_upper'),
        ('kc_middle', 'kc_middle'),
        ('ma5', 'ma15'),
        ('ma15', 'ma5'),
    ):
        frame[target] = 200.0 - source[original]
    long_quote = 200.0 - short_quote
    frame.loc[frame.index[-1], 'close'] = 200.0 - float(source.iloc[-1]['close'])
    frame.loc[frame.index[-1], 'ma5'] = (
        float(frame.loc[frame.index[-2], 'ma5']) + 0.02
    )
    frame.loc[frame.index[-1], 'ma15'] = (
        float(frame.loc[frame.index[-2], 'ma15']) + 0.01
    )
    frame.loc[frame.index[-1], 'open'] = min(
        float(frame.loc[frame.index[-1], 'open']),
        float(frame.loc[frame.index[-1], 'ma15']),
        float(frame.loc[frame.index[-1], 'ma5']),
    )
    return frame, long_quote


def test_large_bearish_body_crossing_both_mas_enters_before_midline_break():
    frame, quote = make_bearish_ma_midline_frame()

    decision = EntryGatePipeline.detect_momentum_burst(
        frame, quote, side='SHORT',
    )

    assert decision is not None
    assert decision['type'] == MOMENTUM_BURST_SHORT_CODE
    assert decision['entry_phase'] == 'KC_MOMENTUM_BURST_SHORT'
    assert decision['body_atr'] >= 0.5
    assert decision['body_ratio'] >= 0.6
    assert decision['midline_distance_atr'] == pytest.approx(0.05)
    assert decision['lower_rail_space_ratio'] >= 0.25


def test_bearish_ma_midline_launch_rejects_more_than_point_one_atr_distance():
    frame, quote = make_bearish_ma_midline_frame(quote=100.1001)

    assert EntryGatePipeline.detect_momentum_burst(
        frame, quote, side='SHORT',
    ) is None


def test_bearish_ma_midline_launch_allows_exact_point_one_atr_distance():
    frame, quote = make_bearish_ma_midline_frame(quote=100.1)

    decision = EntryGatePipeline.detect_momentum_burst(
        frame, quote, side='SHORT',
    )

    assert decision is not None
    assert decision['midline_distance_atr'] == pytest.approx(0.1)


@pytest.mark.parametrize(
    'fault',
    ['ma_tangle', 'kc_squeeze', 'insufficient_lower_space'],
)
def test_bearish_ma_midline_launch_keeps_strict_common_entry_gates(fault):
    frame, quote = make_bearish_ma_midline_frame()
    live = frame.index[-1]
    if fault == 'ma_tangle':
        adjustment = quote - float(frame.loc[live, 'close'])
        live_ma5 = float(frame.loc[live, 'ma5']) + adjustment / 5.0
        frame.loc[live, 'ma15'] = live_ma5 - adjustment / 15.0
    elif fault == 'kc_squeeze':
        frame.loc[live, ['kc_lower', 'kc_middle', 'kc_upper']] = [
            99.75, 100.0, 100.25,
        ]
        frame.loc[live, 'open'] = quote + 0.4
    else:
        frame.loc[live, 'kc_lower'] = 99.85

    if fault == 'kc_squeeze':
        assert strict_kc_entry_problem(frame, quote, 'SHORT') == 'BLOCKED_KC_SQUEEZE'
    assert EntryGatePipeline.detect_momentum_burst(
        frame, quote, side='SHORT',
    ) is None






@pytest.mark.parametrize(
    ('side', 'code'),
    [('LONG', MOMENTUM_BURST_LONG_CODE),
     ('SHORT', MOMENTUM_BURST_SHORT_CODE)],
)
def test_momentum_burst_is_symmetric(side, code):
    frame, quote = make_momentum_burst_frame(side)

    decision = EntryGatePipeline.detect_momentum_burst(
        frame, quote, side=side,
    )

    assert decision is not None
    assert decision['type'] == code
    assert decision['side'] == side
    assert decision['body_atr'] >= 0.5
    assert decision['body_ratio'] >= 0.6
    assert (
        decision['upper_rail_space_ratio'] >= 0.25
        if side == 'LONG'
        else decision['lower_rail_space_ratio'] >= 0.25
    )


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_momentum_burst_requires_fifty_percent_body(side):
    frame, quote = make_momentum_burst_frame(side)
    live = frame.index[-1]
    if side == 'LONG':
        frame.loc[live, 'high'] = quote + 1.1
    else:
        frame.loc[live, 'low'] = quote - 1.1

    assert EntryGatePipeline.detect_momentum_burst(
        frame, quote, side=side,
    ) is None


def test_momentum_burst_allows_exact_fifty_percent_body_and_one_x_wick():
    frame, quote = make_momentum_burst_frame('LONG')
    live = frame.index[-1]
    opening = float(frame.loc[live, 'open'])
    body = quote - opening
    frame.loc[live, ['open', 'close', 'low', 'high']] = [
        opening,
        quote,
        opening,
        quote + body,
    ]
    decision = EntryGatePipeline.detect_momentum_burst(frame, quote, side='LONG')
    assert decision is not None
    assert decision['body_ratio'] == pytest.approx(0.5, abs=1e-9)
    assert decision['upper_rail_space_ratio'] >= 0.15




@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_live_midline_launch_is_symmetric_and_needs_no_bar_close(side):
    frame, quote = make_frame(side)
    detector = (
        EntryGatePipeline.detect_midline_break_long if side == 'LONG'
        else EntryGatePipeline.detect_midline_break_short
    )

    decision = detector(frame, quote, side=side)

    assert decision is not None
    assert decision['side'] == side
    assert decision['entry_phase'] == 'KC_MIDLINE_LAUNCH'
    assert decision['intrabar'] is True
    assert decision['volume_confirmed'] is True
    assert decision['ma5_slope'] > 0 if side == 'LONG' else decision['ma5_slope'] < 0
    assert decision['ma15_slope'] >= 0 if side == 'LONG' else decision['ma15_slope'] <= 0
    assert not bool(frame.iloc[-1]['is_closed'])


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_flat_but_not_adverse_ma15_does_not_delay_midline_launch(side):
    frame, quote = make_frame(side)
    frame.loc[frame.index[-1], 'ma15'] = frame.iloc[-2]['ma15']

    detector = (
        EntryGatePipeline.detect_midline_break_long if side == 'LONG'
        else EntryGatePipeline.detect_midline_break_short
    )

    assert detector(frame, quote, side=side) is not None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_next_bar_midline_retest_recovers_missed_launch(side):
    frame, quote = make_frame(side, phase='retest')
    detector = (
        EntryGatePipeline.detect_midline_break_long if side == 'LONG'
        else EntryGatePipeline.detect_midline_break_short
    )

    decision = detector(frame, quote, side=side)

    assert decision is not None
    assert decision['entry_phase'] == 'KC_MIDLINE_RETEST'
    assert decision['breakout_bar_id'] == frame.iloc[-2]['timestamp']




@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize(
    'fault',
    ['missing_breakout', 'no_midline_test', 'no_recovery', 'wrong_bar_identity'],
)
def test_midline_retest_rejects_incomplete_evidence(side, fault):
    frame, quote = make_frame(side, phase='retest')
    live = frame.index[-1]
    breakout = frame.index[-2]
    middle = float(frame.iloc[-1]['kc_middle'])

    if fault == 'missing_breakout':
        frame.loc[breakout, 'close'] = (
            middle - 0.01 if side == 'LONG' else middle + 0.01
        )
    elif fault == 'no_midline_test':
        frame.loc[live, 'low' if side == 'LONG' else 'high'] = (
            middle + 0.01 if side == 'LONG' else middle - 0.01
        )
    elif fault == 'no_recovery':
        quote = middle - 0.01 if side == 'LONG' else middle + 0.01
        frame.loc[live, 'close'] = quote
    else:
        frame.loc[live, 'timestamp'] += 60_000

    detector = (
        EntryGatePipeline.detect_midline_break_long if side == 'LONG'
        else EntryGatePipeline.detect_midline_break_short
    )
    assert detector(frame, quote, side=side) is None




@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_launch_phase_cannot_claim_retest_exemption(side):
    frame, quote = make_frame(side)
    assert EntryGatePipeline._detect_midline_entry(
        frame, quote, side,
    )['entry_phase'] == 'KC_MIDLINE_LAUNCH'
    assert entry_market_structure_problem(
        frame, quote, side, entry_phase='KC_MIDLINE_RETEST',
    ) == 'BLOCKED_MIDLINE_RETEST_EVIDENCE'


@pytest.mark.parametrize(('side', 'quote'), [('LONG', 103.50), ('SHORT', 96.50)])
def test_midline_entry_rejects_less_than_quarter_channel_room(side, quote):
    frame, _ = make_frame(side, quote=quote)
    detector = (
        EntryGatePipeline.detect_midline_break_long if side == 'LONG'
        else EntryGatePipeline.detect_midline_break_short
    )

    assert detector(frame, quote, side=side) is None


@pytest.mark.parametrize(('side', 'quote'), [('LONG', 102.0), ('SHORT', 98.0)])
def test_midline_entry_allows_exact_quarter_channel_room(side, quote):
    quote = 101.0 if side == 'LONG' else 99.0
    frame, _ = make_frame(side, quote=quote)
    frame.loc[frame.index[-1], 'kc_upper'] = 102.0
    frame.loc[frame.index[-1], 'kc_lower'] = 98.0
    detector = (
        EntryGatePipeline.detect_midline_break_long if side == 'LONG'
        else EntryGatePipeline.detect_midline_break_short
    )

    assert detector(frame, quote, side=side) is not None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_midline_entry_is_blocked_without_volume_expansion(side):
    frame, quote = make_frame(side)
    frame.loc[frame.index[-1], 'volume'] = 149.9
    detector = (
        EntryGatePipeline.detect_midline_break_long if side == 'LONG'
        else EntryGatePipeline.detect_midline_break_short
    )

    assert detector(frame, quote, side=side) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_next_bar_retest_requires_preceding_breakout_bar(side):
    frame, quote = make_frame(side, phase='retest')
    frame.loc[frame.index[-2], 'timestamp'] -= 60_000
    detector = (
        EntryGatePipeline.detect_midline_break_long if side == 'LONG'
        else EntryGatePipeline.detect_midline_break_short
    )

    assert detector(frame, quote, side=side) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_closed_bar_does_not_authorize_delayed_midline_entry(side):
    frame, quote = make_frame(side)
    frame.loc[frame.index[-1], 'is_closed'] = True
    detector = (
        EntryGatePipeline.detect_midline_break_long if side == 'LONG'
        else EntryGatePipeline.detect_midline_break_short
    )

    assert detector(frame, quote, side=side) is None






def test_midline_launch_still_blocks_two_recent_ma_crosses():
    frame, quote = make_frame('LONG')
    closed = frame.index[:-1]
    frame.loc[closed[-3], 'ma5'] = 99.7
    frame.loc[closed[-3], 'ma15'] = 99.8
    frame.loc[closed[-2], 'ma5'] = 99.85
    frame.loc[closed[-2], 'ma15'] = 99.8
    frame.loc[closed[-1], 'ma5'] = 99.9
    frame.loc[closed[-1], 'ma15'] = 99.95
    frame.loc[frame.index[-1], 'ma5'] = 100.0
    frame.loc[frame.index[-1], 'ma15'] = 99.86

    assert ma5_ma15_cross_chop_problem(
        frame, side='LONG',
    ) == 'BLOCKED_MA5_MA15_CROSS_CHOP'
    assert EntryGatePipeline.detect_midline_break_long(
        frame, quote, side='LONG',
    ) is None


