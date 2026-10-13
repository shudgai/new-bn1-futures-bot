"""Three-bar KC authorization used by ordinary breakouts and re-entry."""
import pandas as pd
import pytest

from core.services.three_bar_rail_gate import three_bar_rail_gate_problem
from core.services.entry_contract import evaluate_three_bar_outer_breakout


def frame_for(side, third_open=None, third_low=None, third_high=None):
    if side == 'LONG':
        rows = [
            dict(timestamp=0, open=99., high=101.2, low=98., close=101.,
                 kc_upper=100., kc_lower=98.),
            dict(timestamp=60_000, open=101., high=103., low=100., close=102.5,
                 kc_upper=101.5, kc_lower=99.5),
            dict(timestamp=120_000, open=102.5, high=103.5, low=102., close=103.,
                 kc_upper=102., kc_lower=100.),
        ]
        open_ = 103. if third_open is None else third_open
        low = 102.5 if third_low is None else third_low
        high = 103.5 if third_high is None else third_high
        quote = 103.2 if open_ >= 103. else 102.8
    else:
        rows = [
            dict(timestamp=0, open=101., high=102., low=98.8, close=99.,
                 kc_upper=102., kc_lower=100.),
            dict(timestamp=60_000, open=99., high=100., low=97., close=97.5,
                 kc_upper=100.5, kc_lower=98.5),
            dict(timestamp=120_000, open=97.5, high=98., low=96.5, close=97.,
                 kc_upper=99., kc_lower=98.),
        ]
        open_ = 97. if third_open is None else third_open
        low = 96.5 if third_low is None else third_low
        high = 97.5 if third_high is None else third_high
        quote = 96.8 if open_ <= 97. else 97.2
    rows.append(dict(timestamp=180_000, open=open_, high=high, low=low,
                     close=quote, kc_upper=102.5 if side == 'LONG' else 99.,
                     kc_lower=100. if side == 'LONG' else 97.5, is_closed=False))
    for row in rows:
        row['atr'] = 1.
    frame = pd.DataFrame(rows)
    frame['is_closed'] = [True, True, True, False]
    frame.attrs['timeframe_ms'] = 60_000
    return frame, quote


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_three_bar_directional_third_candle_is_allowed(side):
    frame, quote = frame_for(side)

    assert three_bar_rail_gate_problem(frame, quote, side) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_second_same_color_confirmation_need_not_close_outside_rail(side):
    frame, quote = frame_for(side)
    if side == 'LONG':
        frame.loc[frame.index[-2], 'kc_upper'] = 104.
    else:
        frame.loc[frame.index[-2], 'kc_lower'] = 96.

    decision = evaluate_three_bar_outer_breakout(frame, quote, 'CAP/USDT')

    assert decision is not None
    assert decision['side'] == side


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_small_counter_candle_is_allowed_without_breaking_structure(side):
    frame, quote = frame_for(
        side,
        third_open=103. if side == 'LONG' else 97.,
        third_low=102.5 if side == 'LONG' else 96.,
        third_high=103.5 if side == 'LONG' else 97.5,
    )

    assert three_bar_rail_gate_problem(frame, quote, side) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_counter_candle_cannot_break_two_bar_support_or_resistance(side):
    frame, quote = frame_for(
        side,
        third_open=103. if side == 'LONG' else 97.,
        third_low=97. if side == 'LONG' else 96.,
        third_high=103.5 if side == 'LONG' else 103.,
    )

    assert three_bar_rail_gate_problem(frame, quote, side) in (
        'BLOCKED_THREE_BAR_LONG_SUPPORT_BROKEN',
        'BLOCKED_THREE_BAR_SHORT_RESISTANCE_BROKEN',
    )


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_counter_candle_cannot_exceed_half_atr(side):
    frame, quote = frame_for(
        side,
        third_open=103. if side == 'LONG' else 97.,
        third_low=102. if side == 'LONG' else 96.,
        third_high=103.5 if side == 'LONG' else 98.,
    )
    quote = 102.4 if side == 'LONG' else 97.6
    frame.loc[frame.index[-1], 'close'] = quote

    assert three_bar_rail_gate_problem(frame, quote, side) == (
        'BLOCKED_THREE_BAR_LONG_PULLBACK_TOO_LARGE'
        if side == 'LONG' else 'BLOCKED_THREE_BAR_SHORT_PULLBACK_TOO_LARGE'
    )


def test_three_bar_gate_fails_closed_when_confirmation_is_missing():
    frame = pd.DataFrame([dict(open=100., close=101.)])

    assert three_bar_rail_gate_problem(frame, 101., 'LONG') == 'BLOCKED_THREE_BAR_NOT_READY'
