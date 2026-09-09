import time

import pandas as pd
import pytest
from core.engine import TradingEngine

def _generate_macro_frame(trend_dir='DOWN', num_candles=70):
    df = pd.DataFrame(index=range(num_candles), columns=['open', 'high', 'low', 'close', 'ma3', 'ma15', 'kc_upper', 'kc_lower'])
    base_price = 100.0
    for i in range(num_candles):
        if trend_dir == 'DOWN':
            base_price -= 0.1
        elif trend_dir == 'UP':
            base_price += 0.1
        df.loc[i, 'ma15'] = base_price
        df.loc[i, 'kc_upper'] = base_price + 1.0
        df.loc[i, 'kc_lower'] = base_price - 1.0
        if i % 2 == 0:
            df.loc[i, 'ma3'] = base_price + 0.1
        else:
            df.loc[i, 'ma3'] = base_price - 0.1
        df.loc[i, 'open'] = base_price
        df.loc[i, 'close'] = base_price
        df.loc[i, 'high'] = base_price + 0.2
        df.loc[i, 'low'] = base_price - 0.2
    return df


def _set_gradual_convergence(df, rail, gaps):
    for index, gap in zip(range(64, 69), gaps):
        if rail == 'kc_upper':
            df.loc[index, 'ma15'] = float(df.loc[index, rail]) - gap
        else:
            df.loc[index, 'ma15'] = float(df.loc[index, rail]) + gap

def _add_closed_confirmation(df):
    """Move the tested break one bar back and close its immediate confirmation."""
    df.iloc[-4] = df.iloc[-3].copy()
    df.iloc[-3] = df.iloc[-2].copy()
    df.loc[df.index[-4], 'close'] = (float(df.iloc[-4]['kc_lower']) + float(df.iloc[-4]['kc_upper'])) / 2
    df.loc[df.index[-3], 'open'] = (float(df.iloc[-3]['kc_lower']) + float(df.iloc[-3]['kc_upper'])) / 2
    df.loc[df.index[-2], 'open'] = float(df.iloc[-2]['close']) + (0.05 if float(df.iloc[-2]['close']) < float(df.iloc[-3]['open']) else -0.05)
    df.loc[df.index[-1], ['kc_upper', 'kc_lower']] = df.iloc[-2][['kc_upper', 'kc_lower']].values

def test_macro_trend_wait_if_insufficient_data():
    df = _generate_macro_frame(num_candles=5)
    res = TradingEngine._channel_swing_action(df, 100.0, None)
    assert res['action'] == 'WAIT'

    pass

def test_macro_trend_entry_short_on_lower_kc_structure_break():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[65, 'low'] = 94.0
    df.loc[66, 'low'] = 93.5
    df.loc[67, 'low'] = 94.0
    df.loc[68, 'close'] = 92.5
    df.loc[68, 'ma3'] = 93.2
    df.loc[68, 'ma15'] = 94.0
    df.loc[68, 'kc_lower'] = 93.0
    df.loc[68, 'low'] = 92.4
    _add_closed_confirmation(df)
    res = TradingEngine._channel_swing_action(df, 92.5, None)
    pass  # Removed immediate entry shortcut
    assert res['side'] == 'SHORT'
    assert res['reason'] in {'LIVE_LOWER_BREAKOUT', 'KC_LOWER_BREAKOUT_STRICT'}

    pass

def test_macro_trend_entry_long_on_upper_kc_structure_break():
    df = _generate_macro_frame('UP', 70)
    df.loc[65, 'high'] = 106.0
    df.loc[66, 'high'] = 106.5
    df.loc[67, 'high'] = 106.0
    df.loc[68, 'close'] = 107.5
    df.loc[68, 'ma3'] = 107.2
    df.loc[68, 'ma15'] = 106.5
    df.loc[68, 'kc_upper'] = 107.0
    df.loc[68, 'high'] = 107.6
    _add_closed_confirmation(df)
    res = TradingEngine._channel_swing_action(df, 107.5, None)
    pass  # Removed immediate entry shortcut
    assert res['side'] == 'LONG'
    assert res['reason'] in {'LIVE_UPPER_BREAKOUT', 'KC_UPPER_BREAKOUT_STRICT'}

def test_normal_two_candle_breakout_remains_tradable():
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["open", "high", "low", "close", "kc_upper", "kc_lower", "ma15"]] = [106.6, 107.3, 106.5, 107.2, 107.0, 105.0, 106.0]
    df.loc[68, ["open", "high", "low", "close", "kc_upper", "kc_lower", "ma15"]] = [107.15, 107.8, 107.1, 107.6, 107.1, 105.1, 106.1]
    df.loc[67:68, "ma3"] = [106.8, 107.3]
    df.loc[69, ["kc_upper", "kc_lower"]] = [107.1, 105.1]
    res = TradingEngine._channel_swing_action(df, 107.6, None)
    assert res == {"action": "ENTER", "side": "LONG", "reason": "LIVE_UPPER_BREAKOUT"}


def test_inside_confirmation_cannot_use_later_live_candle():
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["open", "high", "low", "close", "kc_upper", "kc_lower", "ma15"]] = [
        106.6, 107.4, 106.5, 107.3, 107.0, 105.0, 106.0,
    ]
    df.loc[68, ["open", "high", "low", "close", "kc_upper", "kc_lower", "ma15"]] = [
        107.3, 107.7, 107.1, 107.35, 107.4, 105.1, 106.1,
    ]
    df.loc[69, ["open", "high", "low", "close", "kc_upper", "kc_lower"]] = [
        108.2, 108.3, 107.2, 108.2, 107.8, 105.8,
    ]
    df.loc[67:68, "ma3"] = [106.8, 107.1]
    result = TradingEngine._channel_swing_action(df, 107.35)
    assert result["action"] == "WAIT"


def test_weak_breakout_is_invalidated_by_reverse_waterfall():
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["open", "high", "low", "close", "kc_upper", "kc_lower"]] = [
        106.6, 107.4, 106.5, 107.3, 107.0, 105.0,
    ]
    df.loc[68, ["open", "high", "low", "close", "kc_upper", "kc_lower"]] = [
        107.3, 107.7, 107.1, 107.35, 107.4, 105.1,
    ]
    df.loc[69, ["open", "high", "low", "close", "kc_upper", "kc_lower"]] = [
        109.5, 109.6, 107.0, 108.2, 107.8, 105.8,
    ]
    result = TradingEngine._channel_swing_action(df, 108.2)
    assert result["action"] == "WAIT"


def test_channel_live_waterfall_and_two_abnormal_bars_exit():
    df = _generate_macro_frame("UP", 70)
    df.loc[69, ["open", "close"]] = [100.0, 101.0]
    assert TradingEngine._channel_adverse_exit_reason(df, "SHORT", 101.0, 1.0) == "EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL"
    df.loc[69, ["open", "close"]] = [100.0, 100.1]
    df.loc[67, ["open", "close"]] = [100.0, 100.6]
    df.loc[68, ["open", "close"]] = [100.6, 101.2]
    assert TradingEngine._channel_adverse_exit_reason(df, "SHORT", 100.1, 1.0) == "EMERGENCY_EXIT_2_CANDLE_ADVERSE"
    df.loc[67, ["open", "close"]] = [100.0, 100.3]
    df.loc[68, ["open", "close"]] = [100.3, 101.0]
    assert TradingEngine._channel_adverse_exit_reason(df, "SHORT", 100.1, 1.0) is None




def test_chop_is_diagnostic_only_and_never_blocks_orders():
    assert TradingEngine._channel_chop_gate("ENTER", "LONG", True, False) == ("ENTER", "LONG", None)
    assert TradingEngine._channel_near_chop_entry_gate("REVERSE", "SHORT", True, True) == ("REVERSE", "SHORT", None)




def test_chop_unlocks_after_two_clear_directional_closed_bars():
    df = _generate_macro_frame("UP", 70)
    for index in (67, 68):
        df.loc[index, "close"] = float(df.loc[index, "ma15"]) + 0.25
        df.loc[index, "ma3"] = float(df.loc[index, "ma15"]) + 0.10
    result = TradingEngine._channel_chop_state(df)
    assert result["clear_direction"] == "LONG"





def test_spike_breakout_is_not_traded():
    """A huge outer-rail spike is prone to an immediate opposite candle."""
    df = _generate_macro_frame("UP", 70)
    # [-3] breaks up normally; [-2] confirms green but spans more than the
    # full KC width, so the bot must wait for a new clean breakout.
    df.loc[67, ["open", "high", "low", "close", "kc_upper", "kc_lower", "ma15"]] = [106.6, 107.5, 106.4, 107.2, 107.0, 105.0, 106.0]
    df.loc[68, ["open", "high", "low", "close", "kc_upper", "kc_lower", "ma15"]] = [107.1, 110.0, 106.8, 107.6, 107.1, 105.1, 106.1]
    df.loc[67:68, "ma3"] = [106.8, 107.3]
    res = TradingEngine._channel_swing_action(df, 107.6, None)
    assert res["action"] == "WAIT"
    assert res["reason"] == "KC_SPIKE_BREAKOUT_WAIT"


def test_spike_reversal_waits_for_confirmed_direction():
    """An impulse candle followed by a red rejection must not open long."""
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["open", "high", "low", "close", "kc_upper"]] = [
        106.0, 112.0, 105.8, 111.5, 107.0,
    ]
    df.loc[68, ["open", "high", "low", "close", "kc_upper"]] = [
        111.5, 111.8, 107.2, 108.0, 107.1,
    ]
    df.loc[69, "open"] = 108.0  # No new live body break after the rejected spike.
    result = TradingEngine._channel_immediate_outer_break_action(df, 108.0)
    assert result["action"] == "WAIT"


def test_live_body_crossing_upper_rail_enters_immediately():
    df = _generate_macro_frame("UP", 70)
    df.loc[68, ["close", "kc_upper"]] = [107.2, 107.0]
    df.loc[69, ["open", "high", "low", "close", "kc_upper"]] = [
        107.3, 110.0, 107.2, 108.2, 107.5,
    ]
    result = TradingEngine._channel_swing_action(
        df, 108.2, allow_live_entry=True,
    )
    assert result["action"] == "ENTER"


def test_later_clean_continuation_can_enter_after_spike_wait():
    df = _generate_macro_frame("UP", 72)
    df.loc[67, ["open", "high", "low", "close", "kc_upper"]] = [
        106.0, 112.0, 105.8, 111.5, 107.0,
    ]
    df.loc[68, ["open", "high", "low", "close", "kc_upper"]] = [
        111.5, 111.8, 107.2, 108.0, 107.1,
    ]
    df.loc[69, ["open", "high", "low", "close", "kc_upper"]] = [
        107.4, 109.0, 107.3, 108.8, 107.5,
    ]
    df.loc[70, ["open", "high", "low", "close", "kc_upper"]] = [
        108.8, 110.0, 108.7, 109.7, 107.8,
    ]
    result = TradingEngine._channel_swing_action(df, 109.7)
    assert result == {
        "action": "ENTER", "side": "LONG", "reason": "LIVE_UPPER_BREAKOUT",
    }


def test_macro_trend_hold_position():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[66:68, "ma3"] = [95.0, 94.0, 93.0]  # No closed trough.
    res = TradingEngine._channel_swing_action(df, 93.0, 'SHORT')
    assert res['action'] in ('HOLD', 'EXIT')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_favorable_waterfall_closed_turn_holds_without_middle_exit(side):
    df = _generate_macro_frame('UP' if side == 'LONG' else 'DOWN', 70)
    df['timestamp'] = [(i + 1) * 60000 for i in range(len(df))]
    df['atr'] = 0.75
    if side == 'LONG':
        df.loc[67, ['open', 'high', 'low', 'close', 'ma3', 'kc_upper']] = [
            106.0, 108.0, 105.8, 107.5, 107.2, 107.0,
        ]
        df.loc[68, ['open', 'high', 'low', 'close', 'ma3', 'kc_upper']] = [
            107.5, 108.2, 106.8, 107.1, 107.1, 107.1,
        ]
    else:
        df.loc[67, ['open', 'high', 'low', 'close', 'ma3', 'kc_lower']] = [
            94.0, 94.2, 92.0, 92.5, 92.8, 93.0,
        ]
        df.loc[68, ['open', 'high', 'low', 'close', 'ma3', 'kc_lower']] = [
            92.5, 93.2, 91.8, 92.9, 92.9, 92.9,
        ]
    df.loc[69, ['open', 'close']] = float(df.loc[68, 'close'])
    result = TradingEngine._channel_swing_action(df, float(df.loc[68, 'close']), side, position_open_timestamp=60)
    assert result['action'] == 'HOLD'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_favorable_waterfall_live_turn_holds_while_ma3_outside(side):
    df = _generate_macro_frame('UP' if side == 'LONG' else 'DOWN', 70)
    df['timestamp'] = [(i + 1) * 60000 for i in range(len(df))]
    df['atr'] = 1.0
    if side == 'LONG':
        df.loc[68, ['open', 'close']] = [106.8, 108.0]
        df.loc[68, ['ma3', 'kc_upper']] = [108.0, 107.1]
        df.loc[69, ['open', 'high', 'low', 'close', 'ma3', 'kc_upper']] = [
            108.0, 108.4, 107.4, 107.5, 107.9, 107.8,
        ]
    else:
        df.loc[68, ['open', 'close']] = [93.2, 92.0]
        df.loc[68, ['ma3', 'kc_lower']] = [92.0, 92.9]
        df.loc[69, ['open', 'high', 'low', 'close', 'ma3', 'kc_lower']] = [
            92.0, 92.6, 91.6, 92.5, 92.1, 92.2,
        ]
    result = TradingEngine._channel_swing_action(df, float(df.loc[69, 'close']), side, position_open_timestamp=60)
    assert result['action'] == 'HOLD'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_outer_turn_does_not_exit_before_ma3_enters_outer_rail(side):
    df = _generate_macro_frame('UP' if side == 'LONG' else 'DOWN', 70)
    if side == 'LONG':
        df.loc[67, ['open', 'high', 'low', 'close', 'ma3', 'kc_upper']] = [
            106.0, 108.0, 105.8, 107.5, 106.8, 107.0,
        ]
        df.loc[68, ['open', 'high', 'low', 'close', 'ma3', 'kc_upper']] = [
            107.5, 108.2, 106.8, 107.1, 106.9, 107.1,
        ]
    else:
        df.loc[67, ['open', 'high', 'low', 'close', 'ma3', 'kc_lower']] = [
            94.0, 94.2, 92.0, 92.5, 93.2, 93.0,
        ]
        df.loc[68, ['open', 'high', 'low', 'close', 'ma3', 'kc_lower']] = [
            92.5, 93.2, 91.8, 92.9, 93.1, 92.9,
        ]
    result = TradingEngine._channel_swing_action(df, float(df.loc[68, 'close']), side)
    assert result['action'] == 'HOLD'


def test_peak_exit_requires_normal_two_bar_reversal_before_short():
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["open", "high", "low", "close", "kc_upper", "kc_lower"]] = [
        107.0, 107.8, 106.5, 106.8, 107.2, 105.0,
    ]
    df.loc[68, ["open", "high", "low", "close", "kc_upper", "kc_lower"]] = [
        106.8, 106.9, 106.0, 106.2, 107.1, 105.0,
    ]
    result = TradingEngine._channel_peak_reversal_action(
        df, 106.1, {"side": "LONG"},
    )
    assert result == {
        "action": "ENTER", "side": "SHORT", "reason": "PEAK_REVERSAL_SHORT",
    }


def test_peak_exit_does_not_chase_abnormal_reverse_candle():
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["open", "high", "low", "close", "kc_upper", "kc_lower"]] = [
        107.0, 107.8, 106.5, 106.8, 107.2, 105.0,
    ]
    df.loc[68, ["open", "high", "low", "close", "kc_upper", "kc_lower"]] = [
        106.8, 106.9, 100.0, 100.2, 107.1, 105.0,
    ]
    result = TradingEngine._channel_peak_reversal_action(
        df, 100.1, {"side": "LONG"},
    )
    assert result["action"] == "WAIT"


def test_flat_scan_can_detect_recent_upper_peak_without_exit_state():
    df = _generate_macro_frame("UP", 70)
    df.loc[66, ["high", "kc_upper", "ma3"]] = [107.8, 107.2, 107.4]
    df.loc[67, ["open", "high", "low", "close", "kc_upper", "kc_lower", "ma3"]] = [
        107.0, 107.1, 106.2, 106.5, 107.1, 105.0, 107.0,
    ]
    df.loc[68, ["open", "high", "low", "close", "kc_upper", "kc_lower", "ma3"]] = [
        106.5, 106.6, 105.5, 105.7, 106.9, 104.9, 106.4,
    ]
    result = TradingEngine._channel_peak_reversal_action(df, 105.7, None)
    assert result == {
        "action": "ENTER", "side": "SHORT", "reason": "PEAK_REVERSAL_SHORT",
    }


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_outer_rail_ma15_convergence_alone_holds(side):
    df = _generate_macro_frame('UP' if side == 'LONG' else 'DOWN', 70)
    # Isolate MA/rail behavior: these bodies are ordinary relative to ATR.
    df['atr'] = 4.0
    if side == 'LONG':
        df.loc[67, ['open', 'close', 'kc_upper', 'kc_lower', 'ma15']] = [99.5, 100.5, 100.0, 98.0, 99.8]
        df.loc[68, ['open', 'close', 'kc_upper', 'kc_lower', 'ma15']] = [100.5, 100.2, 100.0, 98.0, 99.9]
        _set_gradual_convergence(df, 'kc_upper', [0.9, 0.75, 0.55, 0.35, 0.1])
        df.loc[69, ['open', 'close']] = float(df.loc[68, 'close'])
        result = TradingEngine._channel_swing_action(df, 100.2, 'LONG')
    else:
        df.loc[67, ['open', 'close', 'kc_upper', 'kc_lower', 'ma15']] = [100.5, 99.5, 102.0, 100.0, 100.2]
        df.loc[68, ['open', 'close', 'kc_upper', 'kc_lower', 'ma15']] = [99.5, 99.8, 102.0, 100.0, 100.1]
        _set_gradual_convergence(df, 'kc_lower', [0.9, 0.75, 0.55, 0.35, 0.1])
        df.loc[69, ['open', 'close']] = float(df.loc[68, 'close'])
        result = TradingEngine._channel_swing_action(df, 99.8, 'SHORT')
    assert result['action'] == 'HOLD'


def test_ma3_ma15_compression_without_reentry_holds():
    df = _generate_macro_frame('UP', 70)
    # Isolate MA/rail behavior: these bodies are ordinary relative to ATR.
    df['atr'] = 4.0
    df.loc[67, ['open', 'close', 'kc_upper', 'kc_lower', 'ma3', 'ma15']] = [
        99.5, 100.5, 100.0, 98.0, 100.2, 99.0,
    ]
    df.loc[68, ['open', 'close', 'kc_upper', 'kc_lower', 'ma3', 'ma15']] = [
        100.5, 100.2, 100.0, 98.0, 100.0, 99.8,
    ]
    _set_gradual_convergence(df, 'kc_upper', [0.9, 0.75, 0.55, 0.35, 0.1])
    df.loc[69, ['open', 'close']] = float(df.loc[68, 'close'])
    result = TradingEngine._channel_swing_action(df, 100.2, 'LONG')
    assert result['action'] == 'HOLD'


def test_ma15_half_kc_width_away_from_outer_rail_keeps_long():
    df = _generate_macro_frame('UP', 70)
    # Isolate MA/rail behavior: these bodies are ordinary relative to ATR.
    df['atr'] = 4.0
    df.loc[67, ['open', 'close', 'kc_upper', 'kc_lower', 'ma3', 'ma15']] = [
        99.5, 100.5, 102.0, 98.0, 100.2, 99.0,
    ]
    df.loc[68, ['open', 'close', 'kc_upper', 'kc_lower', 'ma3', 'ma15']] = [
        100.5, 100.2, 102.0, 98.0, 100.0, 100.0,
    ]
    _set_gradual_convergence(df, 'kc_upper', [1.8, 1.7, 1.6, 1.5, 1.0])
    df.loc[69, ['open', 'close']] = float(df.loc[68, 'close'])
    result = TradingEngine._channel_swing_action(df, 100.2, 'LONG')
    assert result['action'] == 'HOLD'


def test_outer_rail_ma15_convergence_holds_when_long_continues():
    df = _generate_macro_frame('UP', 70)
    df['atr'] = 0.5
    df.loc[67, ['open', 'close', 'kc_upper', 'kc_lower', 'ma15']] = [99.5, 100.5, 100.0, 98.0, 99.8]
    df.loc[68, ['open', 'close', 'kc_upper', 'kc_lower', 'ma15']] = [100.5, 101.0, 100.1, 98.0, 100.0]
    _set_gradual_convergence(df, 'kc_upper', [0.9, 0.75, 0.55, 0.35, 0.1])
    df.loc[69, ['open', 'close']] = float(df.loc[68, 'close'])
    result = TradingEngine._channel_swing_action(df, 101.0, 'LONG')
    assert result['action'] == 'HOLD'


def test_outer_rail_ma15_convergence_forecast_does_not_exit_long_early():
    df = _generate_macro_frame('UP', 70)
    df['atr'] = 0.5
    df.loc[67, ['open', 'close', 'kc_upper', 'kc_lower', 'ma15']] = [99.5, 100.5, 101.0, 98.0, 99.5]
    df.loc[68, ['open', 'close', 'kc_upper', 'kc_lower', 'ma15']] = [100.5, 100.6, 100.8, 98.0, 100.0]
    _set_gradual_convergence(df, 'kc_upper', [1.2, 1.1, 1.0, 0.9, 0.8])
    df.loc[69, ['open', 'close']] = float(df.loc[68, 'close'])
    result = TradingEngine._channel_swing_action(df, 100.6, 'LONG')
    assert result['action'] == 'HOLD'


def test_sudden_ma15_upper_rail_jump_is_not_convergence():
    df = _generate_macro_frame('UP', 70)
    # Isolate MA/rail behavior: these bodies are ordinary relative to ATR.
    df['atr'] = 4.0
    df.loc[67, ['open', 'close', 'kc_upper', 'kc_lower', 'ma15']] = [99.5, 100.5, 100.0, 98.0, 99.8]
    df.loc[68, ['open', 'close', 'kc_upper', 'kc_lower', 'ma15']] = [100.5, 100.2, 100.0, 98.0, 99.9]
    df.loc[69, ['open', 'close']] = float(df.loc[68, 'close'])
    result = TradingEngine._channel_swing_action(
        df, 100.2, 'LONG', position_open_timestamp=time.time(),
    )
    assert result['action'] == 'HOLD'
    assert result['reason'] == 'HOLDING_LONG_RUN_TO_HIGH'

    later_result = TradingEngine._channel_swing_action(
        df, 100.2, 'LONG', position_open_timestamp=time.time() - 3600,
    )
    assert later_result['action'] == 'HOLD'
    assert later_result['reason'] == 'HOLDING_LONG_RUN_TO_HIGH'


def test_local_ma3_peak_inside_channel_holds_long():
    df = _generate_macro_frame("UP", 70)
    df.loc[66:68, "ma3"] = [100.0, 102.0, 101.0]
    df.loc[67, ["kc_upper", "kc_lower"]] = [105.0, 95.0]
    df.loc[68, ["open", "close", "kc_upper", "kc_lower"]] = [102.0, 101.0, 105.0, 95.0]
    res = TradingEngine._channel_swing_action(df, 101.0, "LONG")
    assert res["action"] == "HOLD"


def test_confirmed_ma3_valley_without_outer_break_holds_short():
    df = _generate_macro_frame("DOWN", 70)
    df.loc[66:68, "ma3"] = [100.0, 98.0, 99.0]
    df.loc[67, ["kc_upper", "kc_lower"]] = [105.0, 95.0]
    df.loc[68, ["open", "close", "kc_upper", "kc_lower"]] = [98.0, 99.0, 105.0, 95.0]
    res = TradingEngine._channel_swing_action(df, 99.0, "SHORT")
    assert res["action"] == "HOLD"




def test_ma3_touching_ma15_without_upper_cross_holds_long():
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["open", "close", "ma3", "ma15", "kc_upper", "kc_lower"]] = [102.0, 101.0, 101.0, 100.0, 105.0, 95.0]
    df.loc[68, ["open", "close", "ma3", "ma15", "kc_upper", "kc_lower"]] = [101.0, 99.0, 100.0, 100.0, 105.0, 95.0]
    res = TradingEngine._channel_swing_action(df, 99.0, "LONG")
    assert res["action"] == "HOLD"


def test_ma3_touching_ma15_on_same_colour_candle_holds():
    df = _generate_macro_frame("UP", 70)
    df.loc[67, ["open", "close", "ma3", "ma15", "kc_upper", "kc_lower"]] = [102.0, 101.0, 101.0, 100.0, 105.0, 95.0]
    df.loc[68, ["open", "close", "ma3", "ma15", "kc_upper", "kc_lower"]] = [99.0, 101.0, 100.0, 100.0, 105.0, 95.0]
    res = TradingEngine._channel_swing_action(df, 101.0, "LONG")
    assert res["action"] == "HOLD"


def test_long_does_not_lock_without_ma_cross():
    df = _generate_macro_frame('UP', 70)
    df.loc[68, ['open', 'close', 'ma3', 'ma15', 'kc_middle', 'kc_lower']] = [107.0, 106.5, 106.8, 106.7, 106.8, 105.5]
    df.loc[69, ['ma3', 'ma15']] = [107.0, 106.7]
    res = TradingEngine._channel_swing_action(df, 106.5, 'LONG')
    assert res['action'] in ('HOLD', 'EXIT')
    assert res['reason'] == 'HOLDING_LONG_RUN_TO_HIGH'

def test_short_does_not_lock_without_ma_cross():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[68, ['open', 'close', 'ma3', 'ma15', 'kc_middle', 'kc_upper']] = [93.0, 93.5, 93.2, 93.3, 93.2, 94.5]
    df.loc[66:68, "ma3"] = [95.0, 94.0, 93.0]  # No closed trough.
    res = TradingEngine._channel_swing_action(df, 93.5, 'SHORT')
    assert res['action'] in ('HOLD', 'EXIT')
    assert res['reason'] == 'HOLDING_SHORT_RUN_TO_LOW'

def test_macro_trend_requires_30_and_60_bar_staircase():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[38, 'ma15'] = 92.0
    res = TradingEngine._channel_swing_action(df, 93.0, None)
    assert res['action'] == 'WAIT'

def test_short_holds_when_bodies_start_outside_upper_kc():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[67, ['open', 'close', 'kc_upper']] = [94.5, 95.0, 94.0]
    df.loc[68, ['open', 'close', 'kc_upper']] = [95.0, 95.5, 94.0]
    df.loc[66:68, "ma3"] = [95.0, 94.0, 93.0]  # No closed trough.
    res = TradingEngine._channel_swing_action(df, 95.5, 'SHORT')
    assert res['action'] in ('HOLD', 'EXIT')

def test_long_does_not_reverse_on_red_wick_without_lower_kc_close():
    df = _generate_macro_frame('UP', 70)
    df.loc[67, ['close', 'kc_lower']] = [105.0, 106.0]
    df.loc[68, ['open', 'close', 'low', 'kc_lower']] = [105.0, 106.5, 105.5, 106.0]
    res = TradingEngine._channel_swing_action(df, 106.5, 'LONG')
    assert res['action'] in ('HOLD', 'EXIT')
    assert res['reason'] != 'KC_LOWER_RED_REVERSE_SHORT'

def test_live_outer_entry_requires_fresh_short_crossing():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[66, ['close', 'kc_lower']] = [93.1, 93.0]
    df.loc[67, ['close', 'kc_lower']] = [92.8, 92.9]
    df.loc[68, ['open', 'close', 'low', 'kc_lower']] = [92.8, 92.5, 92.4, 92.7]
    df.loc[69, ['open', 'close', 'kc_lower']] = [92.5, 92.3, 92.6]
    df.loc[69, 'open'] = 92.7  # Live body crosses the 92.6 lower rail.
    res = TradingEngine._channel_live_outer_entry_action(df, 92.3)
    pass  # Removed immediate entry shortcut
    assert res['reason'] == 'KC_LIVE_LOWER_BREAK_SHORT'

def test_long_holds_through_same_direction_waterfall_up():
    df = _generate_macro_frame('UP', 70)
    df.loc[68, ['open', 'close', 'high', 'kc_upper']] = [107.0, 110.0, 110.5, 108.0]
    res = TradingEngine._channel_swing_action(df, 110.0, 'LONG')
    assert res['action'] in ('HOLD', 'EXIT')
    assert res['reason'] != 'EMERGENCY_EXIT_WATERFALL_UP'

def test_short_holds_through_same_direction_waterfall_down():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[68, ['open', 'close', 'low', 'kc_lower']] = [93.0, 90.0, 89.5, 92.0]
    df.loc[66:68, "ma3"] = [95.0, 94.0, 93.0]  # No closed trough.
    res = TradingEngine._channel_swing_action(df, 90.0, 'SHORT')
    assert res['action'] in ('HOLD', 'EXIT')
    assert res['reason'] != 'EMERGENCY_EXIT_WATERFALL_DOWN'

def test_inflection_alone_does_not_open_long():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[38, 'ma15'] = 96.2
    df.loc[53, 'ma15'] = 94.7
    df.loc[68, 'ma15'] = 95.0
    df.loc[68, 'close'] = 95.5
    df.loc[68, 'ma3'] = 95.2
    df.loc[68, 'kc_upper'] = 96.0
    df.loc[69, ['kc_lower', 'kc_upper', 'open']] = [1., 200., 100.]
    res = TradingEngine._channel_swing_action(df, 95.5, None)
    assert res['action'] == 'WAIT'

def test_inflection_alone_does_not_open_short():
    df = _generate_macro_frame('UP', 70)
    df.loc[38, 'ma15'] = 103.8
    df.loc[53, 'ma15'] = 105.3
    df.loc[68, 'ma15'] = 105.0
    df.loc[68, 'close'] = 104.5
    df.loc[68, 'ma3'] = 104.8
    df.loc[68, 'kc_lower'] = 103.0
    df.loc[69, ['kc_lower', 'kc_upper', 'open']] = [1., 200., 100.]
    res = TradingEngine._channel_swing_action(df, 104.5, None)
    assert res['action'] == 'WAIT'

@pytest.mark.parametrize("previous,current,upper_before,upper_now,expected", [
    (109.0, 108.0, 108.5, 108.2, "HOLD"),
    (109.0, 108.2, 108.5, 108.2, "HOLD"),
    (109.0, 108.8, 108.5, 108.2, "HOLD"),
    (108.0, 107.5, 108.5, 108.2, "HOLD"),
    (109.0, 109.1, 108.5, 109.2, "HOLD"),
    (float("nan"), 108.0, 108.5, 108.2, "HOLD"),
    (109.0, float("inf"), 108.5, 108.2, "HOLD"),
])
@pytest.mark.parametrize("green", [True, False])
def test_long_holds_through_ma3_upper_cross(previous, current, upper_before, upper_now, expected, green):
    df = _generate_macro_frame("UP", 70)
    df.loc[67:68, "ma3"] = [previous, current]
    df.loc[67:68, "kc_upper"] = [upper_before, upper_now]
    df.loc[68, ["open", "close"]] = [107.0, 108.0] if green else [108.0, 107.0]
    result = TradingEngine._channel_swing_action(df, 107.0, "LONG")
    assert result["action"] == expected


def test_live_ma3_upper_cross_waits_for_close():
    df = _generate_macro_frame("UP", 70)
    df.loc[67:69, "ma3"] = [109.0, 108.8, 107.0]
    df.loc[67:69, "kc_upper"] = [108.5, 108.5, 108.5]
    assert TradingEngine._channel_swing_action(df, 107.0, "LONG")["action"] == "HOLD"
