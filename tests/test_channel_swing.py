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

def _add_closed_confirmation(df):
    """Move the tested break one bar back and close its immediate confirmation."""
    df.iloc[-4] = df.iloc[-3].copy()
    df.iloc[-3] = df.iloc[-2].copy()
    df.loc[df.index[-4], 'close'] = (float(df.iloc[-4]['kc_lower']) + float(df.iloc[-4]['kc_upper'])) / 2
    df.loc[df.index[-3], 'open'] = (float(df.iloc[-3]['kc_lower']) + float(df.iloc[-3]['kc_upper'])) / 2
    df.loc[df.index[-2], 'open'] = df.iloc[-2]['close']

def test_macro_trend_wait_if_insufficient_data():
    df = _generate_macro_frame(num_candles=5)
    res = TradingEngine._channel_swing_action(df, 100.0, None)
    assert res['action'] == 'WAIT'

def test_macro_trend_entry_short_on_pullback():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[66, 'ma3'] = 93.0
    df.loc[67, 'ma3'] = 93.5
    df.loc[68, 'ma3'] = 93.1
    df.loc[68, 'close'] = 93.0
    df.loc[68, 'ma15'] = 93.2
    res = TradingEngine._channel_swing_action(df, 93.0, None)
    assert res['action'] == 'ENTER'
    assert res['side'] == 'SHORT'
    assert 'PEAK' in res['reason']

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
    assert res['action'] == 'WAIT'

def test_macro_trend_entry_long_on_pullback():
    df = _generate_macro_frame('UP', 70)
    df.loc[66, 'ma3'] = 107.0
    df.loc[67, 'ma3'] = 106.5
    df.loc[68, 'ma3'] = 106.9
    df.loc[68, 'close'] = 107.0
    df.loc[68, 'ma15'] = 106.8
    res = TradingEngine._channel_swing_action(df, 107.0, None)
    assert res['action'] == 'ENTER'
    assert res['side'] == 'LONG'
    assert 'TROUGH' in res['reason']

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
    assert res['action'] == 'WAIT'

def test_macro_trend_hold_position():
    df = _generate_macro_frame('DOWN', 70)
    res = TradingEngine._channel_swing_action(df, 93.0, 'SHORT')
    assert res['action'] == 'HOLD'

def test_long_does_not_lock_without_ma_cross():
    df = _generate_macro_frame('UP', 70)
    df.loc[68, ['open', 'close', 'ma3', 'ma15', 'kc_middle', 'kc_lower']] = [107.0, 106.5, 106.8, 106.7, 106.8, 105.5]
    df.loc[69, ['ma3', 'ma15']] = [107.0, 106.7]
    res = TradingEngine._channel_swing_action(df, 106.5, 'LONG')
    assert res['action'] == 'HOLD'
    assert res['reason'] == 'HOLDING_LONG_RUN_TO_HIGH'

def test_short_does_not_lock_without_ma_cross():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[68, ['open', 'close', 'ma3', 'ma15', 'kc_middle', 'kc_upper']] = [93.0, 93.5, 93.2, 93.3, 93.2, 94.5]
    res = TradingEngine._channel_swing_action(df, 93.5, 'SHORT')
    assert res['action'] == 'HOLD'
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
    res = TradingEngine._channel_swing_action(df, 95.5, 'SHORT')
    assert res['action'] == 'HOLD'
    assert res['reason'] == 'HOLDING_SHORT_RUN_TO_LOW'

def test_long_does_not_reverse_on_red_wick_without_lower_kc_close():
    df = _generate_macro_frame('UP', 70)
    df.loc[67, ['close', 'kc_lower']] = [105.0, 106.0]
    df.loc[68, ['open', 'close', 'low', 'kc_lower']] = [105.0, 106.5, 105.5, 106.0]
    res = TradingEngine._channel_swing_action(df, 106.5, 'LONG')
    assert res['action'] == 'HOLD'
    assert res['reason'] != 'KC_LOWER_RED_REVERSE_SHORT'

def test_live_outer_entry_follows_directional_short_continuation():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[66, ['close', 'kc_lower']] = [93.1, 93.0]
    df.loc[67, ['close', 'kc_lower']] = [92.8, 92.9]
    df.loc[68, ['open', 'close', 'low', 'kc_lower']] = [92.8, 92.5, 92.4, 92.7]
    df.loc[69, ['open', 'close', 'kc_lower']] = [92.5, 92.3, 92.6]
    res = TradingEngine._channel_live_outer_entry_action(df, 92.3)
    assert res['action'] == 'ENTER'
    assert res['side'] == 'SHORT'
    assert res['reason'] == 'KC_LIVE_LOWER_BREAK_SHORT'

def test_long_holds_through_same_direction_waterfall_up():
    df = _generate_macro_frame('UP', 70)
    df.loc[68, ['open', 'close', 'high', 'kc_upper']] = [107.0, 110.0, 110.5, 108.0]
    res = TradingEngine._channel_swing_action(df, 110.0, 'LONG')
    assert res['action'] == 'HOLD'
    assert res['reason'] != 'EMERGENCY_EXIT_WATERFALL_UP'

def test_short_holds_through_same_direction_waterfall_down():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[68, ['open', 'close', 'low', 'kc_lower']] = [93.0, 90.0, 89.5, 92.0]
    res = TradingEngine._channel_swing_action(df, 90.0, 'SHORT')
    assert res['action'] == 'HOLD'
    assert res['reason'] != 'EMERGENCY_EXIT_WATERFALL_DOWN'

def test_inflection_alone_does_not_open_long():
    df = _generate_macro_frame('DOWN', 70)
    df.loc[38, 'ma15'] = 96.2
    df.loc[53, 'ma15'] = 94.7
    df.loc[68, 'ma15'] = 95.0
    df.loc[68, 'close'] = 95.5
    df.loc[68, 'ma3'] = 95.2
    df.loc[68, 'kc_upper'] = 96.0
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
    res = TradingEngine._channel_swing_action(df, 104.5, None)
    assert res['action'] == 'WAIT'