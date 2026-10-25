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


def test_short_profit_lock_is_allowed_without_midline_reclaim():
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
    assert should_exit is True
    assert reason == 'EXIT_BY_RATCHET_PROFIT_LOCK'


def test_bearish_short_suppresses_ordinary_exit_but_allows_valley_and_lock(monkeypatch):
    valley = _frame([
        dict(timestamp=60_000, open=96.0, high=96.5, low=95.0, close=95.5,
             is_closed=True),
        dict(timestamp=120_000, open=95.5, high=96.0, low=90.0, close=91.0,
             is_closed=True),
        dict(timestamp=180_000, open=91.0, high=94.0, low=90.5, close=94.0,
             is_closed=True),
    ])
    pullback = _frame([
        dict(timestamp=60_000, open=96.0, high=96.5, low=95.0, close=95.5,
             is_closed=True),
        dict(timestamp=120_000, open=95.5, high=96.0, low=90.0, close=91.0,
             is_closed=True),
        dict(timestamp=180_000, open=91.0, high=94.0, low=89.0, close=94.0,
             is_closed=True),
    ])
    monkeypatch.setattr(
        ExitGate, '_evaluate_with_holding_rules',
        staticmethod(lambda *args: (True, 'SHORT_EXIT_KC_MID_BREACH', {'x': 1})),
    )
    result = ExitGate.evaluate(
        {'side': 'SHORT'}, {'macro_trend': 'BEARISH', 'raw_df': pullback},
    )
    assert result[0] is False
    assert result[1] == 'VETO_HOLDING_UNTIL_TRUE_PIVOT'
    assert result[2]['suppressed_exit_reason'] == 'SHORT_EXIT_KC_MID_BREACH'

    monkeypatch.setattr(
        ExitGate, '_evaluate_with_holding_rules',
        staticmethod(lambda *args: (True, 'EXIT_BY_CONFIRMED_TRUE_VALLEY', {})),
    )
    assert ExitGate.evaluate(
        {'side': 'SHORT'}, {'macro_trend': 'BEARISH', 'raw_df': valley},
    )[0] is True
    monkeypatch.setattr(
        ExitGate, '_evaluate_with_holding_rules',
        staticmethod(lambda *args: (True, 'EXIT_BY_RATCHET_PROFIT_LOCK', {})),
    )
    assert ExitGate.evaluate({'side': 'SHORT'}, {'macro_trend': 'BEARISH'})[0] is True


def test_bullish_long_mirror_holds_until_peak(monkeypatch):
    peak = _frame([
        dict(timestamp=60_000, open=101.0, high=102.0, low=100.0, close=101.5,
             is_closed=True),
        dict(timestamp=120_000, open=104.0, high=110.0, low=103.5, close=109.0,
             is_closed=True),
        dict(timestamp=180_000, open=109.0, high=109.5, low=102.0, close=103.0,
             is_closed=True),
    ])
    monkeypatch.setattr(
        ExitGate, '_evaluate_with_holding_rules',
        staticmethod(lambda *args: (True, 'LONG_EXIT_KC_MID_BREACH', {})),
    )
    result = ExitGate.evaluate({'side': 'LONG'}, {'macro_trend': 'BULLISH'})
    assert result[0] is False
    assert result[1] == 'VETO_HOLDING_UNTIL_TRUE_PIVOT'
    monkeypatch.setattr(
        ExitGate, '_evaluate_with_holding_rules',
        staticmethod(lambda *args: (True, 'EXIT_BY_CONFIRMED_TRUE_PEAK', {})),
    )
    assert ExitGate.evaluate(
        {'side': 'LONG'}, {'macro_trend': 'BULLISH', 'raw_df': peak},
    )[0] is True
