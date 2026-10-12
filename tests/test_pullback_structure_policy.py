import pandas as pd
import pytest

from core.gates.holding_protection_gate import (
    confirmed_midline_reversal_evidence,
    confirmed_short_bottom_evidence,
)
from core.gates.pipeline import pipeline


@pytest.mark.parametrize(
    ('side', 'previous_close', 'previous_mid', 'opening', 'close', 'middle', 'ma15'),
    [
        ('LONG', 101.0, 100.5, 100.8, 99.8, 100.2, 100.1),
        ('SHORT', 99.0, 99.5, 99.2, 100.2, 99.8, 99.9),
    ],
)
def test_large_closed_body_crossing_middle_or_ma15_confirms_reversal(
    side, previous_close, previous_mid, opening, close, middle, ma15,
):
    frame = pd.DataFrame([
        dict(timestamp=60_000, open=previous_close, close=previous_close,
             high=previous_close + .2, low=previous_close - .2,
             atr=1., kc_middle=previous_mid, ma15=previous_mid,
             is_closed=True),
        dict(timestamp=120_000, open=opening, close=close,
             high=max(opening, close) + .1, low=min(opening, close) - .1,
             atr=1., kc_middle=middle, ma15=ma15, is_closed=True),
    ])

    assert confirmed_midline_reversal_evidence(frame, side) is not None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_intrabar_wick_or_small_closed_body_is_not_midline_reversal(side):
    if side == 'LONG':
        previous_close, previous_mid = 101., 100.5
        opening, close, low, middle, ma15 = 100.8, 100.3, 99.8, 100.2, 100.1
    else:
        previous_close, previous_mid = 99., 99.5
        opening, close, high, middle, ma15 = 99.2, 99.7, 100.2, 99.8, 99.9
    live = dict(timestamp=120_000, open=opening, close=close, atr=1.,
                kc_middle=middle, ma15=ma15, is_closed=False)
    live['low' if side == 'LONG' else 'high'] = low if side == 'LONG' else high
    frame = pd.DataFrame([
        dict(timestamp=60_000, open=previous_close, close=previous_close,
             high=previous_close + .2, low=previous_close - .2,
             atr=1., kc_middle=previous_mid, ma15=previous_mid,
             is_closed=True),
        live,
    ])

    assert confirmed_midline_reversal_evidence(frame, side) is None


def test_short_trough_requires_volume_spike_wick_or_two_defended_lows():
    bars = [
        dict(timestamp=i * 60_000, open=100. - i, high=100.2 - i,
             low=99.8 - i, close=100. - i, atr=1., ma5=99.5 - i,
             volume=100., is_closed=True)
        for i in range(6)
    ]
    bars[-1].update(open=96., high=96.2, low=94.8, close=95.9,
                    ma5=96.1, volume=250.)
    evidence = confirmed_short_bottom_evidence(pd.DataFrame(bars))
    assert evidence['kind'] == 'VOLUME_CLIMAX_LOWER_WICK'
    assert evidence['wick_ratio'] >= .45

    bars[-3]['low'], bars[-2]['low'], bars[-1]['low'] = 95., 95.1, 95.2
    bars[-1].update(close=96.2, ma5=96.1, volume=100.)
    evidence = confirmed_short_bottom_evidence(pd.DataFrame(bars))
    assert evidence['kind'] == 'TWO_DEFENDED_LOWS_MA5_RECLAIM'


def test_downtrend_pullback_resume_short_enters_first_small_break_body():
    rows = []
    for i, (mid, ma5, ma15, low) in enumerate([
        (10.6, 10.4, 10.7, 10.0),
        (10.5, 10.3, 10.6, 9.95),
        (10.4, 10.2, 10.5, 9.9),
        (10.3, 10.1, 10.4, 9.85),
    ]):
        rows.append(dict(
            timestamp=(i + 1) * 60_000, open=10.2, high=10.3,
            low=low, close=10.15, atr=1., kc_middle=mid,
            kc_upper=11., kc_lower=9., ma5=ma5, ma15=ma15,
            is_closed=True,
        ))
    rows.append(dict(
        timestamp=300_000, open=9.9, high=9.95, low=9.65,
        close=9.75, atr=1., kc_middle=10.2, kc_upper=10.9,
        kc_lower=9.0, ma5=10.0, ma15=10.3, is_closed=False,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000

    decision = pipeline.detect_downtrend_pullback_resume_short(frame, 9.75)

    assert decision is not None
    assert decision['type'] == 'KC_DOWNTREND_PULLBACK_RESUME_SHORT'
    assert decision['side'] == 'SHORT'
    assert decision['realtime_body_atr'] == pytest.approx(.15)
