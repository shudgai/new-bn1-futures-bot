from types import SimpleNamespace

import pandas as pd
import pytest

from core.services.entry_contract import (
    CLIMAX_REVERSAL_FLIP_CODE,
    evaluate_climax_flip_entry,
    evaluate_entry_contract,
)
from core.services.exits.dual_track_exit_service import (
    DualTrackExitStrategy,
    detect_climax_reversal,
)
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing


def climax_frame(prior_side='LONG', *, extended=True):
    live_ms = 1_800_000
    rows = []
    for i in range(11):
        if prior_side == 'LONG':
            opening, close = 100. + i, 101. + i
            high, low = close + .2, opening - .1
            upper, lower = 101. + i, 98. + i
        else:
            opening, close = 110. - i, 109. - i
            high, low = opening + .1, close - .2
            upper, lower = 113. - i, 109. - i
        if not extended:
            opening = 100. + (i % 2) * .05
            close = opening + (.05 if prior_side == 'LONG' else -.05)
            high, low = max(opening, close) + .05, min(opening, close) - .05
            upper, lower = 102., 98.
        rows.append(dict(
            timestamp=live_ms - 720_000 + i * 60_000,
            is_closed=True, open=opening, high=high, low=low, close=close,
            atr=1., ma5=close - .2 if prior_side == 'LONG' else close + .2,
            kc_lower=lower, kc_middle=(upper + lower) / 2, kc_upper=upper,
        ))

    previous = rows[-1]
    if prior_side == 'LONG':
        rev = dict(open=111.2, high=112., low=109.5, close=109.8,
                   ma5=110.5, kc_lower=108., kc_middle=110., kc_upper=111.5)
    else:
        rev = dict(open=98.8, high=100.5, low=97.5, close=100.2,
                   ma5=99.5, kc_lower=98., kc_middle=100., kc_upper=102.)
    if not extended:
        if prior_side == 'LONG':
            rev = dict(open=100.06, high=100.1, low=99.9, close=99.99,
                       ma5=100.04, kc_lower=98., kc_middle=100., kc_upper=102.)
        else:
            rev = dict(open=99.94, high=100.1, low=99.9, close=100.01,
                       ma5=99.96, kc_lower=98., kc_middle=100., kc_upper=102.)
    rows.append(dict(
        timestamp=live_ms - 60_000, is_closed=True, atr=1.,
        **rev,
    ))
    rows.append(dict(
        timestamp=live_ms, is_closed=False, open=rev['close'],
        high=rev['close'] + .1, low=rev['close'] - .1,
        close=rev['close'], atr=1., ma5=rev['ma5'],
        kc_lower=rev['kc_lower'], kc_middle=rev['kc_middle'],
        kc_upper=rev['kc_upper'],
    ))
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame


@pytest.mark.parametrize(('prior_side', 'expected_side', 'expected_reason'), [
    ('LONG', 'SHORT', 'CLIMAX_REVERSAL_EXIT_TOP'),
    ('SHORT', 'LONG', 'CLIMAX_REVERSAL_EXIT_BOTTOM'),
])
def test_climax_requires_directional_extension_and_engulfing_reversal(
    prior_side, expected_side, expected_reason,
):
    frame = climax_frame(prior_side)
    evidence = detect_climax_reversal(frame, prior_side)
    assert evidence['side'] == expected_side
    assert evidence['reason'] == expected_reason
    assert evidence['body_atr'] >= .8
    assert evidence['extension_atr'] >= 3.0
    assert DualTrackExitStrategy().evaluate_exit(
        {'side': prior_side}, frame, current_price=float(frame.iloc[-1]['close']),
    ) == expected_reason


@pytest.mark.parametrize('prior_side', ['LONG', 'SHORT'])
def test_small_range_engulfing_does_not_trigger_climax_exit(prior_side):
    frame = climax_frame(prior_side, extended=False)
    assert detect_climax_reversal(frame, prior_side) is None
    live_ms = float(frame.iloc[-1]['timestamp'])
    close_action = 'CLOSE_LONG' if prior_side == 'LONG' else 'CLOSE_SHORT'
    account = SimpleNamespace(positions={}, trades=[dict(
        id=live_ms + 1000, symbol='CAP/USDT', action=close_action,
        status='CLOSED', reason='ordinary MA pullback close',
    )])
    assert evaluate_climax_flip_entry(
        frame, float(frame.iloc[-1]['close']), 'CAP/USDT', account,
    ) is None
    assert evaluate_entry_contract(
        frame, float(frame.iloc[-1]['close']), CLIMAX_REVERSAL_FLIP_CODE,
        account=account, symbol='CAP/USDT',
    ) is None


@pytest.mark.parametrize(('prior_side', 'field', 'value'), [
    ('LONG', 'high', 113.0),
    ('SHORT', 'low', 96.0),
])
def test_missing_true_swing_extreme_or_kc_touch_blocks_flip(prior_side, field, value):
    frame = climax_frame(prior_side)
    swing_index = frame.index[-4]
    frame.loc[swing_index, field] = value
    assert detect_climax_reversal(frame, prior_side) is None


@pytest.mark.parametrize(('prior_side', 'rail', 'value'), [
    ('LONG', 'kc_upper', 113.),
    ('SHORT', 'kc_lower', 96.5),
])
def test_extreme_must_touch_the_matching_kc_rail(prior_side, rail, value):
    frame = climax_frame(prior_side)
    frame.loc[frame.index[-2], rail] = value
    assert detect_climax_reversal(frame, prior_side) is None


@pytest.mark.parametrize(('old_side', 'new_side', 'close_action'), [
    ('LONG', 'SHORT', 'CLOSE_LONG'),
    ('SHORT', 'LONG', 'CLOSE_SHORT'),
])
def test_only_same_minute_climax_close_can_authorize_flip(old_side, new_side, close_action):
    frame = climax_frame(old_side)
    live_ms = float(frame.iloc[-1]['timestamp'])
    reason = ('CLIMAX_REVERSAL_EXIT_TOP' if old_side == 'LONG'
              else 'CLIMAX_REVERSAL_EXIT_BOTTOM')
    account = SimpleNamespace(positions={}, trades=[dict(
        id=live_ms + 1000, symbol='CAP/USDT', action=close_action,
        status='CLOSED', reason='Channel Swing ' + reason,
    )])

    decision = evaluate_entry_contract(
        frame, float(frame.iloc[-1]['close']), CLIMAX_REVERSAL_FLIP_CODE,
        account=account, symbol='CAP/USDT',
    )
    assert decision['side'] == new_side
    assert decision['entry_phase'] == 'EXTREME_CLIMAX_FLIP'
    if new_side == 'SHORT':
            assert decision['initial_sl'] == pytest.approx(112.2)
    else:
            assert decision['initial_sl'] == pytest.approx(97.3)

    account.trades[0]['reason'] = 'Channel Swing ordinary profit close'
    diagnostics = {}
    assert evaluate_entry_contract(
        frame, float(frame.iloc[-1]['close']), CLIMAX_REVERSAL_FLIP_CODE,
        account=account, symbol='CAP/USDT', diagnostics=diagnostics,
    ) is None
    assert diagnostics['reason'] == 'BLOCKED_CLIMAX_FLIP_NO_MATCHED_EXTREME_REVERSAL_CLOSE'


def test_climax_stop_breach_rejects_flip_order():
    frame = climax_frame('LONG')
    live_ms = float(frame.iloc[-1]['timestamp'])
    account = SimpleNamespace(positions={}, trades=[dict(
        id=live_ms + 1000, symbol='CAP/USDT', action='CLOSE_LONG',
        status='CLOSED', reason='Channel Swing CLIMAX_REVERSAL_EXIT_TOP',
    )])
    diagnostics = {}
    assert evaluate_entry_contract(
        frame, 112.2, CLIMAX_REVERSAL_FLIP_CODE,
        account=account, symbol='CAP/USDT', diagnostics=diagnostics,
    ) is None
    assert diagnostics['reason'] == 'BLOCKED_CLIMAX_FLIP_NO_MATCHED_EXTREME_REVERSAL_CLOSE'


@pytest.mark.parametrize(('side', 'entry', 'stop', 'breach'), [
    ('SHORT', 104., 105.8, 105.8),
    ('LONG', 106., 104.3, 104.3),
])
def test_flip_structural_stop_is_active_for_channel_swing(side, entry, stop, breach):
    position = dict(
        symbol='CAP/USDT', side=side, entry_mode='CHANNEL_SWING',
        entry_phase='EXTREME_CLIMAX_FLIP', entry_price=entry, qty=1.,
        atr=1., entry_atr=1., initial_sl=stop, open_timestamp=1_700.,
    )
    result = evaluate_peak_trailing(position, breach, 1_800_000)
    assert result['trigger'] == 'INITIAL_ATR'
    assert result['type'] == 'EXIT_INITIAL_ATR_HARD_STOP'
