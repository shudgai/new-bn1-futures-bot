import pandas as pd

from core.gates.swing_origin_entry_gate import outer_pivot_evidence
from core.services.entry_contract import _small_body_impulse_confirmation


def _pivot_frame(side):
    rows = [
        dict(timestamp=60_000, open=100.0, high=100.5, low=99.5, close=100.0,
             atr=1.0, ma5=100.0, ma15=100.0, kc_lower=98.0,
             kc_middle=100.0, kc_upper=102.0, is_closed=True),
    ]
    if side == 'LONG':
        rows.extend([
            dict(timestamp=120_000, open=98.5, high=99.0, low=97.8, close=98.2,
                 atr=1.0, ma5=98.0, ma15=98.0, kc_lower=98.0,
                 kc_middle=100.0, kc_upper=102.0, is_closed=True),
            dict(timestamp=180_000, open=98.2, high=98.5, low=98.1, close=98.4,
                 atr=1.0, ma5=98.3, ma15=98.1, kc_lower=98.0,
                 kc_middle=100.0, kc_upper=102.0, is_closed=True),
        ])
        quote = 98.45
    else:
        rows.extend([
            dict(timestamp=120_000, open=101.5, high=102.2, low=101.0, close=101.8,
                 atr=1.0, ma5=102.0, ma15=102.0, kc_lower=98.0,
                 kc_middle=100.0, kc_upper=102.0, is_closed=True),
            dict(timestamp=180_000, open=101.8, high=101.9, low=101.5, close=101.6,
                 atr=1.0, ma5=101.7, ma15=101.9, kc_lower=98.0,
                 kc_middle=100.0, kc_upper=102.0, is_closed=True),
        ])
        quote = 101.55
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame, quote


def test_outer_kc_trough_requires_confirmed_compact_reversal_for_long():
    frame, quote = _pivot_frame('LONG')
    evidence = outer_pivot_evidence(frame, quote, 'LONG')
    assert evidence is not None
    assert evidence['pivot_bar_id'] == 120_000
    assert evidence['confirmation_bar_id'] == 180_000


def test_outer_kc_peak_requires_confirmed_compact_reversal_for_short():
    frame, quote = _pivot_frame('SHORT')
    evidence = outer_pivot_evidence(frame, quote, 'SHORT')
    assert evidence is not None
    assert evidence['pivot_bar_id'] == 120_000
    assert evidence['confirmation_bar_id'] == 180_000


def test_outer_kc_pivot_is_not_authorized_when_confirmation_is_not_compact():
    frame, quote = _pivot_frame('LONG')
    frame.loc[2, 'close'] = 99.0
    assert outer_pivot_evidence(frame, 99.0, 'LONG') is None


def test_oversized_impulse_requires_small_directional_next_bar_close():
    short_green = dict(open=100.0, high=100.2, low=99.9, close=100.2)
    long_red = dict(open=100.2, high=100.3, low=99.8, close=100.0)
    assert _small_body_impulse_confirmation(short_green, 'LONG', 100.1, 1.0)
    assert _small_body_impulse_confirmation(long_red, 'SHORT', 100.1, 1.0)
    larger_valid = dict(open=100.0, high=100.9, low=99.9, close=100.8)
    assert _small_body_impulse_confirmation(larger_valid, 'LONG', 100.1, 1.0)
    too_large = dict(open=100.0, high=101.2, low=99.9, close=101.1)
    assert not _small_body_impulse_confirmation(too_large, 'LONG', 100.1, 1.0)
    too_short = dict(open=100.0, high=100.1, low=99.99, close=100.1)
    assert not _small_body_impulse_confirmation(too_short, 'LONG', 100.05, 1.0)
