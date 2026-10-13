import pytest
import pandas as pd
from unittest.mock import Mock, AsyncMock

from core.services.exits.hybrid_dynamic_exit import (
    evaluate_hybrid_dynamic_exit,
    HYBRID_DYNAMIC_ATR_TRAILING_TRIGGER,
    HYBRID_DYNAMIC_RATCHET_TRIGGER,
    HYBRID_MIDLINE_BREACH_TRIGGER,
    HYBRID_DYNAMIC_STATE_KEY,
)
from core.gates.holding_protection_gate import (
    HoldingProtectionExitGate,
    _last_closed_close_at_or_above_kc_middle,
)


def make_candle_df(closes, middles):
    rows = []
    for i, (c, m) in enumerate(zip(closes, middles)):
        rows.append({
            'timestamp': 1791860000000.0 + i * 60000,
            'open': c - 0.0001,
            'high': c + 0.0002,
            'low': c - 0.0002,
            'close': c,
            'kc_upper': m + 0.0005,
            'kc_middle': m,
            'kc_lower': m - 0.0005,
            'atr': 0.0003,
            'ma5': c,
            'ma15': m,
        })
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    return df


def test_holding_protection_veto_when_above_kc_middle_for_long():
    """Verify that any regular pullback exit is strictly vetoed if close >= kc_middle for LONG."""
    position = {
        'symbol': '龙虾/USDT',
        'side': 'LONG',
        'entry_price': 0.0430,
        'qty': 1000.0,
        'entry_mode': 'CHANNEL_SWING',
    }
    # Latest closed bar is 0.0432, KC middle is 0.0425 -> strictly above
    frame = make_candle_df([0.0431, 0.0432], [0.0425, 0.0425])
    
    assert _last_closed_close_at_or_above_kc_middle(frame) is True

    # Validate THREE_POINT_PIVOT candidate exit
    is_allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, 0.0431, 'THREE_POINT_PIVOT', channel_swing=True,
    )
    assert not is_allowed
    assert reason == 'VETO_CLOSE_ABOVE_KC_MIDDLE_HOLDING_PROTECTION'


def test_holding_protection_veto_when_below_kc_middle_for_short():
    """Verify that any regular pullback exit is strictly vetoed if close <= kc_middle for SHORT."""
    position = {
        'symbol': 'CAP/USDT',
        'side': 'SHORT',
        'entry_price': 0.0420,
        'qty': 1000.0,
        'entry_mode': 'CHANNEL_SWING',
    }
    # Latest closed bar is 0.0415, KC middle is 0.0425 -> strictly below
    frame = make_candle_df([0.0418, 0.0415], [0.0425, 0.0425])
    
    assert _last_closed_close_at_or_above_kc_middle(frame) is False

    is_allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, 0.0416, 'THREE_POINT_PIVOT', channel_swing=True,
    )
    assert not is_allowed
    assert reason == 'VETO_CLOSE_BELOW_KC_MIDDLE_HOLDING_PROTECTION'


def test_hybrid_dynamic_stage1_outer_rail_breakout_and_1_3_atr_trailing():
    """Verify that when price breaks out beyond KC upper band, Stage 1 is armed and exits on 1.3 ATR pullback."""
    position = {
        'symbol': '龙虾/USDT',
        'side': 'LONG',
        'entry_price': 0.0430,
        'qty': 1000.0,
        'open_timestamp': 1791860000.0,
    }
    meta = {}
    snapshot = {
        'live_kc_upper': 0.0433,
        'live_kc_middle': 0.0428,
        'live_kc_lower': 0.0423,
        'atr': 0.0002,
        'history_5': [],
    }

    # Tick 1: Price surges out of KC upper rail to 0.04360 -> arms Stage 1
    trigger, changed, diag = evaluate_hybrid_dynamic_exit(
        position, meta, 0.04360, 1791860060000.0, snapshot,
    )
    assert trigger is None
    assert diag['stage'] == 1
    assert diag['peak_high'] == 0.04360

    # Tick 2: Price retraces by 0.5 ATR to 0.04350 -> should still hold
    trigger, changed, diag = evaluate_hybrid_dynamic_exit(
        position, meta, 0.04350, 1791860070000.0, snapshot,
    )
    assert trigger is None

    # Tick 3: Price drops to 0.04360 - 1.3 * 0.0002 = 0.04334 (or lower: 0.04330) -> triggers HYBRID_DYNAMIC_ATR_TRAILING
    trigger, changed, diag = evaluate_hybrid_dynamic_exit(
        position, meta, 0.04330, 1791860080000.0, snapshot,
    )
    assert trigger == HYBRID_DYNAMIC_ATR_TRAILING_TRIGGER


def test_hybrid_dynamic_kc_midline_close_breach():
    """Verify that an adverse closed candle breaching KC midline triggers KC_MIDLINE_CLOSE_BREACH."""
    position = {
        'symbol': '龙虾/USDT',
        'side': 'LONG',
        'entry_price': 0.0430,
        'qty': 1000.0,
        'open_timestamp': 1791860000.0,
    }
    meta = {}
    # Adverse red candle closing at 0.0424 below kc_middle 0.0428
    snapshot = {
        'live_kc_upper': 0.0433,
        'live_kc_middle': 0.0428,
        'live_kc_lower': 0.0423,
        'atr': 0.0002,
        'history_5': [{
            'o': 0.0429,
            'h': 0.0430,
            'l': 0.0423,
            'c': 0.0424,
            'kc_middle': 0.0428,
        }],
    }

    trigger, changed, diag = evaluate_hybrid_dynamic_exit(
        position, meta, 0.0424, 1791860120000.0, snapshot,
    )
    assert trigger == HYBRID_MIDLINE_BREACH_TRIGGER
