import pandas as pd

from core.gates.holding_protection_gate import HoldingProtectionExitGate
from core.services.exits.realtime_profit_exit import (
    SHORT_FALSE_BREAKOUT_TRIGGER,
    _evaluate_realtime_core_exit_gates,
)


def short_position():
    return {
        'side': 'SHORT',
        'entry_mode': 'CHANNEL_SWING',
        'open_timestamp': 60.0,
        'entry_price': 99.5,
        'qty': 1.0,
        'margin': 100.0,
        'entry_snapshot': {
            'signal_code': 'AUTHORIZED_REALTIME_BREAKOUT',
            'entry_bar_id': 60_000.0,
            'entry_bar_open': 100.0,
        },
    }


def test_short_is_cut_when_next_green_bar_reclaims_breakout_open():
    snapshot = {
        'reason': None,
        'quote_ms': 121_000.0,
        'live_bar_ms': 120_000.0,
        'closed_bar_ms': 60_000.0,
        'live_open': 100.5,
        'live_low': 100.4,
        'live_high': 102.0,
        'live_kc_lower': 98.0,
        'live_kc_upper': 104.0,
        'live_kc_middle': 101.0,
        'atr': 1.0,
        'history_5': [],
    }
    reason, _ = _evaluate_realtime_core_exit_gates(
        short_position(), {}, 101.0, 121_000.0, snapshot, 0.0005, 0.0001,
    )
    assert reason == SHORT_FALSE_BREAKOUT_TRIGGER


def test_false_break_exit_requires_the_immediately_following_minute():
    position = short_position()
    snapshot = {
        'reason': None,
        'live_bar_ms': 180_000.0,
        'closed_bar_ms': 120_000.0,
        'live_open': 100.5,
    }
    from core.services.exits.realtime_profit_exit import short_false_breakout_exit

    assert not short_false_breakout_exit(position, {}, 101.0, snapshot)


def test_exit_gate_validates_live_bar_and_original_breakout_open():
    frame = pd.DataFrame([
        {
            'timestamp': 60_000.0, 'open': 100.0, 'high': 100.2,
            'low': 98.5, 'close': 99.0, 'atr': 1.0,
            'kc_middle': 100.0, 'is_closed': True,
        },
        {
            'timestamp': 120_000.0, 'open': 100.5, 'high': 101.5,
            'low': 100.4, 'close': 101.0, 'atr': 1.0,
            'kc_middle': 100.8, 'is_closed': False,
        },
    ])
    allowed, reason, details = HoldingProtectionExitGate.validate_exit(
        short_position(), frame, 101.0, SHORT_FALSE_BREAKOUT_TRIGGER,
    )
    assert allowed is True
    assert reason == SHORT_FALSE_BREAKOUT_TRIGGER
    assert details['entry_bar_open'] == 100.0


def test_exit_gate_rejects_quote_below_breakout_open():
    frame = pd.DataFrame([
        {
            'timestamp': 60_000.0, 'open': 100.0, 'high': 100.2,
            'low': 98.5, 'close': 99.0, 'atr': 1.0,
            'kc_middle': 100.0, 'is_closed': True,
        },
        {
            'timestamp': 120_000.0, 'open': 99.5, 'high': 100.5,
            'low': 99.4, 'close': 100.0, 'atr': 1.0,
            'kc_middle': 100.8, 'is_closed': False,
        },
    ])
    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        short_position(), frame, 100.0, SHORT_FALSE_BREAKOUT_TRIGGER,
    )
    assert allowed is False
    assert reason == HoldingProtectionExitGate.WAIT_CLOSE_REJECT_REASON
