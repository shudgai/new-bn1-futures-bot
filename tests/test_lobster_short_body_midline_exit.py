import pandas as pd

from core.gates.unified_pipeline_gates import (
    ExitGate,
    _short_midline_body_reclaim_confirmed,
)


def _frame(rows):
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame


def test_live_upper_wick_through_kc_middle_does_not_confirm_short_exit():
    frame = _frame([
        dict(timestamp=60_000, open=99.0, high=99.4, low=98.7, close=99.1,
             is_closed=True),
        dict(timestamp=120_000, open=99.1, high=99.3, low=98.8, close=99.0,
             is_closed=True),
        dict(timestamp=180_000, open=99.0, high=101.0, low=98.9, close=99.2,
             is_closed=False),
    ])
    assert not _short_midline_body_reclaim_confirmed(frame, 100.0)


def test_closed_body_above_kc_middle_confirms_short_exit():
    frame = _frame([
        dict(timestamp=60_000, open=99.0, high=99.4, low=98.7, close=99.1,
             is_closed=True),
        dict(timestamp=120_000, open=99.1, high=100.4, low=99.0, close=100.2,
             is_closed=True),
        dict(timestamp=180_000, open=100.2, high=100.4, low=100.1, close=100.25,
             is_closed=False),
    ])
    assert _short_midline_body_reclaim_confirmed(frame, 100.0)


def test_short_profit_lock_waits_while_closed_body_remains_below_middle():
    frame = _frame([
        dict(timestamp=60_000, open=98.0, high=98.2, low=97.5, close=98.0,
             is_closed=True),
        dict(timestamp=120_000, open=95.5, high=95.6, low=92.0, close=92.5,
             is_closed=True),
        dict(timestamp=180_000, open=92.5, high=93.2, low=91.0, close=93.0,
             is_closed=True),
        dict(timestamp=240_000, open=93.0, high=100.5, low=92.8, close=93.5,
             is_closed=False),
    ])
    position = {
        'side': 'SHORT', 'entry_price': 100.0, 'lowest_price': 91.0,
        'qty': 1.0, 'leverage': 5.0,
    }
    snapshot = {
        'current_price': 93.0, 'atr': 2.0, 'kc_upper': 105.0,
        'kc_middle': 100.0, 'kc_lower': 95.0, 'kc_middle_slope': -0.05,
        'ma15': 97.0, 'ema_fast': 96.0, 'ema_slow': 98.0,
        'raw_df': frame, 'macro_trend': 'BEARISH',
    }
    should_exit, reason, meta = ExitGate.evaluate(
        position, snapshot, current_price=93.0,
    )
    assert should_exit is False
    assert 'VETO_HOLD_SHORT' in reason
    assert meta['suppressed_exit_reason'] == 'EXIT_BY_RATCHET_PROFIT_LOCK'
