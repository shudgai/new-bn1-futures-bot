import time
from types import SimpleNamespace

import pandas as pd
import pytest

from core.services import entry_contract
from core.services.strategies.unified_entry_strategy import (
    ANTI_BOTTOM_SHORT_BOUNCE_BODY_ATR,
    ANTI_BOTTOM_SHORT_COOLDOWN_BARS,
    ANTI_BOTTOM_SHORT_POST_CLOSE_REBOUND_ATR,
    ANTI_BOTTOM_SHORT_STRETCH_ATR,
    ANTI_BOTTOM_SHORT_WICK_BODY_RATIO,
    anti_bottom_short_problem,
    evaluate_closed_entry,
    short_kc_trend_problem,
)


def short_frame(*, live=True):
    stamp = int(time.time() // 60) * 60_000
    rows = []
    for offset in range(5, 0, -1):
        rows.append(dict(
            timestamp=stamp - offset * 60_000,
            is_closed=True,
            open=100.2, high=100.3, low=99.8, close=100.0,
            atr=1.0, ma3=99.7, ma5=100.0 - offset * .1,
            ma15=100.5, kc_lower=97.9 + offset * .1,
            kc_middle=99.5 + offset * .1, kc_upper=102.0,
            volume=10.0,
        ))
    rows.append(dict(
        timestamp=stamp,
        is_closed=not live,
        open=98.1, high=98.3, low=97.8, close=97.9,
        atr=1.0, ma3=99.7, ma5=99.4, ma15=100.5,
        kc_lower=97.9, kc_middle=99.5, kc_upper=102.0, volume=12.0,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame


def test_stretch_below_lower_band_blocks_short():
    frame = short_frame()
    lower = float(frame.iloc[-1]['kc_lower'])
    assert anti_bottom_short_problem(frame, lower - 0.21) is None
    assert anti_bottom_short_problem(frame, lower - ANTI_BOTTOM_SHORT_STRETCH_ATR - .001) == (
        'BLOCKED_SHORT_KC_LOWER_STRETCH'
    )


def test_flat_or_rising_smoothed_kc_trend_blocks_every_short():
    frame = short_frame()
    assert short_kc_trend_problem(frame) is None
    frame.loc[frame.index[-3:], 'kc_middle'] = 100.0
    assert short_kc_trend_problem(frame) == 'BLOCKED_SHORT_KC_TREND_NOT_DOWN'


def test_below_band_short_waits_for_a_measurable_retest_rejection():
    frame = short_frame()
    frame.loc[frame.index[-1], ['high', 'low']] = [98.0, 97.85]
    assert anti_bottom_short_problem(frame, 97.85) == 'BLOCKED_SHORT_WAIT_PULLBACK_REJECTION'

    frame.loc[frame.index[-1], 'high'] = 98.3
    assert anti_bottom_short_problem(frame, 97.85) is None


@pytest.mark.parametrize('event', ['lower_wick', 'ma3_reclaim'])
def test_recent_exhaustion_or_strong_bounce_starts_three_bar_cooldown(event):
    frame = short_frame()
    row = frame.index[-2]
    if event == 'lower_wick':
        frame.loc[row, ['open', 'high', 'low', 'close']] = [100.0, 100.1, 98.9, 99.8]
        assert 100.0 - 98.9 > ANTI_BOTTOM_SHORT_WICK_BODY_RATIO * abs(99.8 - 100.0)
    else:
        frame.loc[row, ['open', 'high', 'low', 'close', 'ma3', 'atr']] = [
            99.0, 100.2, 98.9, 100.0, 99.5, 1.0,
        ]
        assert 100.0 - 99.0 >= ANTI_BOTTOM_SHORT_BOUNCE_BODY_ATR
    assert anti_bottom_short_problem(frame, 97.9) == 'BLOCKED_SHORT_EXHAUSTION_COOLDOWN'
    assert ANTI_BOTTOM_SHORT_COOLDOWN_BARS == 3


def test_confirmed_rail_break_tolerates_bar2_lower_wick():
    frame = short_frame()
    frame.loc[frame.index[-2], ['open', 'high', 'low', 'close']] = [
        100.0, 100.1, 98.9, 99.8,
    ]

    assert anti_bottom_short_problem(frame, 97.9) == (
        'BLOCKED_SHORT_EXHAUSTION_COOLDOWN'
    )
    assert anti_bottom_short_problem(
        frame, 97.9, allow_confirmed_rail_break=True,
    ) is None


def test_short_close_requires_half_atr_rebound_from_post_close_low():
    frame = short_frame()
    frame.loc[frame.index[-2], ['open', 'high', 'low', 'close']] = [
        98.0, 98.0, 97.5, 97.6,
    ]
    account = SimpleNamespace(trades=[dict(
        id=float(frame.iloc[-3].timestamp) + 1000,
        symbol='CAP/USDT', action='CLOSE_SHORT', status='CLOSED', price=98.2,
    )])

    assert anti_bottom_short_problem(
        frame, 97.9, account=account, symbol='CAP/USDT',
    ) == 'BLOCKED_SHORT_POST_CLOSE_NO_PULLBACK'

    frame.loc[frame.index[-2], 'low'] = 97.0
    assert anti_bottom_short_problem(
        frame, 97.9, account=account, symbol='CAP/USDT',
    ) is None
    assert ANTI_BOTTOM_SHORT_POST_CLOSE_REBOUND_ATR == .50


def test_unified_closed_entry_applies_anti_bottom_gate():
    frame = short_frame(live=False)
    ok, reason, _ = evaluate_closed_entry(frame, 'SHORT', price=97.69)
    assert not ok
    assert reason == 'REJECT_SHORT_CLOSE_INSIDE_KC'


def test_entry_contract_final_candidate_applies_anti_bottom_gate(monkeypatch):
    frame = short_frame(live=False)
    frame['is_closed'] = True
    frame.loc[frame.index[-1], 'is_closed'] = False
    frame.loc[frame.index[-1], ['open', 'high', 'low', 'close']] = [
        98.1, 98.2, 97.69, 97.69,
    ]
    # A live quote outside the band cannot replace a completed Bar 1 rail break.
    monkeypatch.setattr(entry_contract, 'evaluate_golden_cross_fast_lane', lambda *_a, **_k: None)
    monkeypatch.setattr(entry_contract, 'evaluate_bearish_instant_breakout', lambda *_a, **_k: None)
    monkeypatch.setattr(entry_contract, 'evaluate_continuation_entry', lambda *_a, **_k: None)
    monkeypatch.setattr(entry_contract, 'detect_raw_triggers', lambda *_a, **_k: ('SHORT', 'TRIGGER_A_KC_BREAKOUT'))
    monkeypatch.setattr(entry_contract, 'evaluate_three_bar_outer_breakout', lambda *_a, **_k: dict(
        action='ENTER', side='SHORT', type='TRIGGER_A_KC_BREAKOUT',
        entry_phase='KC_THREE_BAR_BREAKOUT',
    ))
    monkeypatch.setattr(entry_contract, 'check_entry_gates', lambda *_a, **_k: (True, 'PASSED'))
    monkeypatch.setattr(entry_contract, 'excessive_upper_shadow_problem', lambda *_a, **_k: None)
    diagnostics = {}

    assert entry_contract.evaluate_entry_contract(frame, diagnostics=diagnostics) is None
    assert diagnostics['reason'] == 'BLOCKED_INSIDE_KC_BANDS'


def test_upper_shadow_uses_latest_completed_candle_not_live_tick():
    frame = short_frame()
    frame.loc[frame.index[-1], 'high'] = 105.0

    assert entry_contract.excessive_upper_shadow_problem(
        frame, 99.0, 'LONG',
    ) is None


def test_upper_shadow_still_blocks_excessive_completed_candle():
    frame = short_frame()
    frame.loc[frame.index[-2], ['open', 'high', 'low', 'close']] = [
        100.0, 101.0, 99.8, 100.5,
    ]

    assert entry_contract.excessive_upper_shadow_problem(
        frame, 100.5, 'LONG',
    ) == 'BLOCKED_BY_EXCESSIVE_UPPER_SHADOW'


def test_strong_long_breakout_allows_one_body_of_upper_shadow():
    frame = short_frame()
    frame.loc[frame.index[-4:-1], 'kc_middle'] = [99.5, 99.7, 99.9]
    frame.loc[frame.index[-3:-1], ['open', 'close', 'ma5']] = [
        [100.0, 100.5, 100.2],
        [100.1, 100.6, 100.3],
    ]
    frame.loc[frame.index[-2], 'high'] = 101.0

    assert entry_contract.excessive_upper_shadow_problem(
        frame, 100.6, 'LONG', trigger_type='TRIGGER_A_KC_BREAKOUT',
    ) is None


def test_strong_trend_wick_exception_does_not_apply_to_standard_entry():
    frame = short_frame()
    frame.loc[frame.index[-4:-1], 'kc_middle'] = [99.5, 99.7, 99.9]
    frame.loc[frame.index[-3:-1], ['open', 'close', 'ma5']] = [
        [100.0, 100.5, 100.2],
        [100.1, 100.6, 100.3],
    ]
    frame.loc[frame.index[-2], 'high'] = 101.0

    assert entry_contract.excessive_upper_shadow_problem(
        frame, 100.6, 'LONG', trigger_type='TRIGGER_B_MA_CROSS',
    ) == 'BLOCKED_BY_EXCESSIVE_UPPER_SHADOW'
