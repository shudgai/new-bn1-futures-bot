import pandas as pd
import pytest

from core.gates.holding_protection_gate import (
    CHANNEL_PEAK_PROFIT_EXIT,
    HoldingProtectionExitGate,
    short_lower_break_exit_confirmation,
)


def lower_break_frame(*, quote, closed=False, bullish_reclaim=False):
    rows = [
        dict(
            timestamp=60_000, is_closed=True,
            open=102.0, high=102.2, low=101.0, close=101.2,
            atr=1.0, kc_upper=104.0, kc_middle=100.0, kc_lower=96.0,
            ma5=101.0, ma15=101.5,
        ),
        dict(
            timestamp=120_000, is_closed=True,
            open=101.0, high=101.2, low=100.0, close=100.2,
            atr=1.0, kc_upper=104.0, kc_middle=100.0, kc_lower=96.0,
            ma5=100.5, ma15=101.0,
        ),
        dict(
            timestamp=180_000, is_closed=closed,
            open=100.0,
            high=max(100.2, quote),
            low=94.0,
            close=quote,
            atr=1.0, kc_upper=104.0, kc_middle=100.0, kc_lower=96.0,
            ma5=98.0, ma15=99.0,
        ),
    ]
    if bullish_reclaim:
        rows[-1].update(is_closed=True, close=94.0)
        rows.append(dict(
            timestamp=240_000, is_closed=True,
            open=95.5, high=97.2, low=94.0, close=96.5,
            atr=1.0, kc_upper=104.0, kc_middle=100.0, kc_lower=96.0,
            ma5=97.0, ma15=98.0,
        ))
    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame


@pytest.mark.parametrize('quote', [94.0, 96.099])
def test_strong_bearish_lower_break_waits_below_35_percent_rebound(quote):
    evidence = short_lower_break_exit_confirmation(
        lower_break_frame(quote=quote), quote,
    )

    assert evidence['active'] is True
    assert evidence['confirmed'] is False
    assert evidence['rebound_ratio'] < 0.35


def test_exact_35_percent_rebound_unlocks_short_exit():
    quote = 96.1
    evidence = short_lower_break_exit_confirmation(
        lower_break_frame(quote=quote), quote,
    )

    assert evidence['confirmed'] is True
    assert evidence['confirmation'] == 'INTRABAR_REBOUND_35_PERCENT'
    assert evidence['rebound_ratio'] == pytest.approx(0.35)


def test_closed_bullish_candle_back_inside_lower_rail_unlocks_short_exit():
    quote = 96.5
    evidence = short_lower_break_exit_confirmation(
        lower_break_frame(
            quote=quote, closed=True, bullish_reclaim=True,
        ),
        quote,
    )

    assert evidence['confirmed'] is True
    assert evidence['confirmation'] == 'BULLISH_CLOSE_RECLAIMED_KC_LOWER'


def test_closed_bullish_candle_still_outside_lower_rail_keeps_short_open():
    frame = lower_break_frame(
        quote=95.5, closed=True, bullish_reclaim=True,
    )
    frame.iloc[-1, frame.columns.get_loc('open')] = 95.0
    frame.iloc[-1, frame.columns.get_loc('close')] = 95.5
    frame.iloc[-1, frame.columns.get_loc('high')] = 95.7

    evidence = short_lower_break_exit_confirmation(frame, 95.5)

    assert evidence['active'] is True
    assert evidence['confirmed'] is False


def test_strategy_exit_is_blocked_while_large_bearish_break_is_extending(monkeypatch):
    frame = lower_break_frame(quote=94.0)
    position = {
        'symbol': 'CAP/USDT',
        'side': 'SHORT',
        'entry_price': 101.0,
        'entry_mode': 'CHANNEL_SWING',
    }
    monkeypatch.setattr(
        'core.gates.holding_protection_gate.channel_peak_profit_exit_evidence',
        lambda *_args: (
            {'reason': CHANNEL_PEAK_PROFIT_EXIT},
            False,
        ),
    )

    allowed, reason, details = HoldingProtectionExitGate.validate_exit(
        position, frame, 94.0, CHANNEL_PEAK_PROFIT_EXIT,
        channel_swing=True,
    )

    assert allowed is False
    assert reason == HoldingProtectionExitGate.SHORT_BREAK_HOLD_REJECT_REASON
    assert details['short_lower_break_hold']['confirmed'] is False


def test_lower_break_before_position_open_does_not_hold_current_exit():
    frame = lower_break_frame(quote=94.0)
    frame.attrs['timeframe_ms'] = 60_000
    position = {
        'side': 'SHORT',
        'open_timestamp': 240.0,
    }

    evidence = short_lower_break_exit_confirmation(
        frame, 94.0, position,
    )

    assert evidence['active'] is False


def test_hard_stop_is_not_blocked_by_short_lower_break_guard():
    frame = lower_break_frame(quote=94.0)
    position = {
        'symbol': 'CAP/USDT',
        'side': 'SHORT',
        'entry_price': 101.0,
    }

    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, 94.0, 'ACCOUNT_HARD_STOP',
    )

    assert allowed is True
    assert reason == 'EXIT_BY_CIRCUIT_BREAKER_HARD_SL'


def test_channel_swing_hard_stop_is_not_blocked_by_strategy_branch():
    frame = lower_break_frame(quote=94.0)
    position = {
        'symbol': 'CAP/USDT',
        'side': 'SHORT',
        'entry_price': 101.0,
        'entry_mode': 'CHANNEL_SWING',
    }

    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, 94.0, 'ACCOUNT_HARD_STOP',
        channel_swing=True,
    )

    assert allowed is True
    assert reason == 'EXIT_BY_CIRCUIT_BREAKER_HARD_SL'
