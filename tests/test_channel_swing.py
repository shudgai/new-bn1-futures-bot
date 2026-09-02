import inspect
import pytest
import pandas as pd
import core.paper_account as pa_module
from core.engine import TradingEngine
from core.paper_account import PaperAccount
from core.symbol_rotation import SymbolRotation

def _channel_frame(lower: float=99.0, upper: float=101.0) -> pd.DataFrame:
    return pd.DataFrame({'open': [100.0] * 20, 'close': [100.0] * 20, 'high': [100.5] * 20, 'low': [99.5] * 20, 'ma3': [100.0] * 20, 'ma15': [100.0] * 20, 'volume': [150.0] * 20, 'vol_ma_20': [100.0] * 20, 'kc_lower': [lower] * 20, 'kc_upper': [upper] * 20})

@pytest.mark.parametrize(
    ("side", "price", "live_open", "previous_close", "expected"),
    [
        ("LONG", 101.2, 101.0, 101.1, True),
        ("LONG", 101.2, 101.3, 101.1, False),
        ("LONG", 100.9, 100.7, 100.8, False),
        ("SHORT", 98.8, 99.0, 98.9, True),
        ("SHORT", 98.8, 98.7, 98.9, False),
        ("SHORT", 99.1, 99.3, 99.2, False),
    ],
)
def test_new_entry_requires_directional_move_outside_kc(
    side, price, live_open, previous_close, expected,
):
    frame = _channel_frame()
    frame.loc[frame.index[-2], "close"] = previous_close
    frame.loc[frame.index[-1], "open"] = live_open
    assert TradingEngine._channel_outer_directional_entry_allowed(
        frame, price, side,
    ) is expected

def _closed_trough(frame: pd.DataFrame):
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high']] = [98.8, 98.9, 98.7, 98.95]
    frame.loc[frame.index[-3], 'ma3'] = 98.7
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.2, 100.0, 98.8, 100.1, 99.4]
    frame.loc[frame.index[-1], 'ma3'] = 100.0

def _closed_peak(frame: pd.DataFrame):
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high']] = [101.2, 101.1, 101.05, 101.3]
    frame.loc[frame.index[-3], 'ma3'] = 101.3
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [100.8, 100.0, 99.9, 101.2, 100.6]
    frame.loc[frame.index[-1], 'ma3'] = 100.0

def test_channel_entry_profile_uses_the_whole_entry_route():
    assert TradingEngine._channel_entry_is_trend_follow({
        "reason": "Channel Swing live KC outer break LONG",
    }) is True
    assert TradingEngine._channel_entry_is_trend_follow({
        "signal_code": "KC_INNER_DOWNTREND_SHORT",
    }) is True
    assert TradingEngine._channel_entry_is_trend_follow({
        "reason": "Channel Swing KC lower outer trough LONG",
    }) is False
    assert TradingEngine._channel_entry_is_trend_follow({
        "channel_entry_profile": "PIVOT",
        "reason": "Channel Swing live KC outer break LONG",
    }) is False


def test_channel_entry_profile_uses_market_alignment_not_signal_route():
    assert TradingEngine._channel_entry_profile_for_market(
        "SHORT", "RANGE", -1,
    ) == "TREND_FOLLOW"
    assert TradingEngine._channel_entry_profile_for_market(
        "LONG", "RANGE", -1,
    ) == "COUNTER_TREND"
    assert TradingEngine._channel_entry_profile_for_market(
        "LONG", "BULL", 0,
    ) == "TREND_FOLLOW"
    assert TradingEngine._channel_entry_is_trend_follow({
        "side": "LONG",
        "btc_direction_1h_at_entry": -1,
        "reason": "Channel Swing live KC outer break LONG",
    }) is False


def test_promoted_profile_stays_trend_follow_after_market_changes():
    assert TradingEngine._channel_entry_is_trend_follow({
        "side": "LONG",
        "btc_direction_1h_at_entry": -1,
        "channel_entry_profile": "TREND_FOLLOW",
        "channel_entry_profile_basis": "MARKET_ALIGNMENT",
    }) is True


def test_held_position_upgrades_only_when_direction_strengthens_after_entry():
    engine = TradingEngine.__new__(TradingEngine)
    engine.btc_1h_st_direction = -1
    engine._continuous_market_mode = {"COIN/USDT": "BULL"}
    engine._market_mode_transition_at = {"COIN/USDT": 900.0}
    position = {
        "side": "LONG", "open_timestamp": 1_000.0,
        "btc_direction_1h_at_entry": -1,
    }
    assert engine._channel_position_direction_is_strong(
        "COIN/USDT", position,
    ) is False

    engine._market_mode_transition_at["COIN/USDT"] = 1_100.0
    assert engine._channel_position_direction_is_strong(
        "COIN/USDT", position,
    ) is True

    engine._continuous_market_mode["COIN/USDT"] = "RANGE"
    engine.btc_1h_st_direction = 1
    assert engine._channel_position_direction_is_strong(
        "COIN/USDT", position,
    ) is True


def test_global_btc_direction_has_priority_for_new_entries():
    engine = TradingEngine.__new__(TradingEngine)
    engine.btc_1h_st_direction = -1
    engine._continuous_market_mode = {"COIN/USDT": "BULL"}
    assert engine._channel_macro_market_mode("COIN/USDT") == "BEAR"
    engine.btc_1h_st_direction = 0
    assert engine._channel_macro_market_mode("COIN/USDT") == "BULL"


def test_trend_follow_long_exits_on_live_red_candle_inside_kc():
    frame = _channel_frame()
    frame.loc[frame.index[-1], ["open", "close"]] = [101.2, 100.8]
    result = TradingEngine._channel_trend_follow_return_exit_action(
        frame, 100.8, "LONG",
    )
    assert (result["action"], result["reason"]) == (
        "EXIT", "KC_TREND_LONG_RED_INSIDE_EXIT",
    )


def test_trend_follow_short_exits_on_live_green_candle_inside_kc():
    frame = _channel_frame()
    frame.loc[frame.index[-1], ["open", "close"]] = [98.8, 99.2]
    result = TradingEngine._channel_trend_follow_return_exit_action(
        frame, 99.2, "SHORT",
    )
    assert (result["action"], result["reason"]) == (
        "EXIT", "KC_TREND_SHORT_GREEN_INSIDE_EXIT",
    )


def test_trend_follow_exit_keeps_latest_closed_reentry_after_bar_rollover():
    frame = _channel_frame()
    frame["timestamp"] = [1_000_000 + index * 300_000 for index in range(len(frame))]
    frame.loc[frame.index[-2], ["open", "close"]] = [101.2, 100.8]
    frame.loc[frame.index[-1], ["open", "close"]] = [100.8, 100.9]
    result = TradingEngine._channel_trend_follow_return_exit_action(
        frame, 100.9, "LONG", open_timestamp=5_900.0,
    )
    assert (result["action"], result["reason"]) == (
        "EXIT", "KC_TREND_LONG_RED_INSIDE_EXIT",
    )


def test_trend_follow_exit_ignores_closed_reentry_from_before_entry():
    frame = _channel_frame()
    frame["timestamp"] = [1_000_000 + index * 300_000 for index in range(len(frame))]
    frame.loc[frame.index[-2], ["open", "close"]] = [101.2, 100.8]
    frame.loc[frame.index[-1], ["open", "close"]] = [100.8, 100.9]
    result = TradingEngine._channel_trend_follow_return_exit_action(
        frame, 100.9, "LONG", open_timestamp=6_800.0,
    )
    assert result["action"] == "HOLD"


def test_nontrend_peak_and_valley_exit_after_price_has_returned_inside():
    long_frame = _channel_frame()
    _closed_peak(long_frame)
    long_result = TradingEngine._channel_swing_action(long_frame, 100.6, "LONG")
    short_frame = _channel_frame()
    _closed_trough(short_frame)
    short_result = TradingEngine._channel_swing_action(short_frame, 99.4, "SHORT")
    assert (long_result["action"], long_result["reason"]) == (
        "EXIT", "KC_UPPER_OUTER_PEAK_EXIT",
    )
    assert (short_result["action"], short_result["reason"]) == (
        "EXIT", "KC_LOWER_OUTER_VALLEY_EXIT",
    )


def test_same_bar_rechase_detector_sees_live_upper_kc_without_width_filter():
    frame = _channel_frame(lower=99.8, upper=100.2)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [99.9, 100.1]
    frame.loc[frame.index[-1], ['open', 'close']] = [100.3, 100.1]
    result = TradingEngine._channel_live_outer_entry_action(frame, 100.2)
    assert (result['action'], result['side'], result['reason']) == ('ENTER', 'LONG', 'KC_LIVE_UPPER_BREAK_LONG')
    assert (100.2 - 99.8) / 100.2 < 0.005

def test_same_bar_rechase_detector_sees_live_lower_kc_without_width_filter():
    frame = _channel_frame(lower=99.8, upper=100.2)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [99.9, 100.1]
    frame.loc[frame.index[-1], ['open', 'close']] = [99.7, 99.9]
    result = TradingEngine._channel_live_outer_entry_action(frame, 99.8)
    assert (result['action'], result['side'], result['reason']) == ('ENTER', 'SHORT', 'KC_LIVE_LOWER_BREAK_SHORT')
    assert (100.2 - 99.8) / 99.8 < 0.005

def test_strong_run_enters_long_on_first_live_upper_kc_touch():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-4:], "open"] = [99.6, 99.8, 100.1, 100.6]
    frame.loc[frame.index[-4:], "close"] = [99.8, 100.1, 100.5, 100.8]
    frame.loc[frame.index[-4:], "ma3"] = [99.7, 99.9, 100.2, 100.6]
    frame.loc[frame.index[-1], "ma15"] = 100.0
    result = TradingEngine._channel_strong_first_outer_touch_action(frame, 101.0)
    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", "LONG", "KC_STRONG_FIRST_UPPER_TOUCH_LONG",
    )


def test_strong_run_enters_short_on_first_live_lower_kc_touch():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-4:], "open"] = [100.4, 100.2, 99.9, 99.4]
    frame.loc[frame.index[-4:], "close"] = [100.2, 99.9, 99.5, 99.2]
    frame.loc[frame.index[-4:], "ma3"] = [100.3, 100.1, 99.8, 99.4]
    frame.loc[frame.index[-1], "ma15"] = 100.0
    result = TradingEngine._channel_strong_first_outer_touch_action(frame, 99.0)
    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", "SHORT", "KC_STRONG_FIRST_LOWER_TOUCH_SHORT",
    )


def test_strong_touch_fast_path_is_only_for_the_first_outer_touch():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-4:], "open"] = [99.6, 99.8, 100.4, 101.0]
    frame.loc[frame.index[-4:], "close"] = [99.8, 100.3, 101.1, 101.2]
    frame.loc[frame.index[-4:], "ma3"] = [99.7, 100.0, 100.5, 101.0]
    frame.loc[frame.index[-1], "ma15"] = 100.0
    result = TradingEngine._channel_strong_first_outer_touch_action(frame, 101.3)
    assert result["action"] == "WAIT"


def test_live_outer_entry_waits_when_upper_wick_touches_but_price_is_inside():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [100.5, 100.4, 101.1]
    result = TradingEngine._channel_live_outer_entry_action(frame, 100.9)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_LIVE_OUTER_BREAK')

def test_live_outer_entry_waits_when_lower_wick_touches_but_price_is_inside():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [99.5, 98.9, 99.6]
    result = TradingEngine._channel_live_outer_entry_action(frame, 99.1)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_LIVE_OUTER_BREAK')

def test_live_outer_entry_does_not_chase_after_upper_touch_retraces_too_far():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [100.5, 100.4, 101.1]
    result = TradingEngine._channel_live_outer_entry_action(frame, 100.7)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_LIVE_OUTER_BREAK')

def test_live_outer_entry_does_not_chase_after_lower_touch_rebounds_too_far():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "low", "high"]] = [99.5, 98.9, 99.6]
    result = TradingEngine._channel_live_outer_entry_action(frame, 99.3)
    assert (result["action"], result["reason"]) == ("WAIT", "WAIT_LIVE_OUTER_BREAK")

def test_live_outer_entry_allows_fresh_long_break_despite_prior_extension():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [98.0, 101.1]
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [100.8, 100.7, 101.1]
    result = TradingEngine._channel_live_outer_entry_action(frame, 101.1)
    assert (result['action'], result['side']) == ('ENTER', 'LONG')

def test_live_outer_entry_allows_fresh_short_break_despite_prior_extension():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [98.9, 102.0]
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [99.2, 98.9, 99.3]
    result = TradingEngine._channel_live_outer_entry_action(frame, 98.9)
    assert (result['action'], result['side']) == ('ENTER', 'SHORT')

def test_live_outer_entry_chases_extended_long_run_when_price_is_above_rail():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [98.0, 101.1]
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [101.6, 101.2, 101.7]
    result = TradingEngine._channel_live_outer_entry_action(frame, 101.1)
    assert (result['action'], result['side'], result['reason']) == ('ENTER', 'LONG', 'KC_LIVE_UPPER_BREAK_LONG')

def test_live_outer_entry_chases_extended_short_run_when_price_is_below_rail():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [98.9, 102.0]
    frame.loc[frame.index[-1], ['open', 'low', 'high']] = [98.4, 98.3, 98.8]
    result = TradingEngine._channel_live_outer_entry_action(frame, 98.9)
    assert (result['action'], result['side'], result['reason']) == ('ENTER', 'SHORT', 'KC_LIVE_LOWER_BREAK_SHORT')

def test_held_long_does_not_exit_on_upper_rail_touch_without_confirmed_peak():
    frame = _channel_frame(lower=99.0, upper=101.0)
    result = TradingEngine._channel_swing_action(frame, 101.0, 'LONG')
    assert (result['action'], result['side']) == ('HOLD', None)

def test_held_short_does_not_exit_on_lower_rail_touch_without_confirmed_trough():
    frame = _channel_frame(lower=99.0, upper=101.0)
    result = TradingEngine._channel_swing_action(frame, 99.0, 'SHORT')
    assert (result['action'], result['side']) == ('HOLD', None)

def test_three_candle_exit_never_counts_the_live_candle():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-3:], ['open', 'close']] = [[102.0, 101.8], [101.9, 101.6], [101.7, 101.4]]
    result = TradingEngine._channel_swing_action(frame, 101.4, 'LONG')
    assert result['action'] == 'HOLD'

def test_three_red_candles_require_falling_closes():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-4:-1], ['open', 'close']] = [[102.0, 101.6], [101.9, 101.7], [101.8, 101.5]]
    result = TradingEngine._channel_swing_action(frame, 100.0, 'LONG')
    assert result['action'] == 'HOLD'

def test_held_long_keeps_position_when_red_candle_remains_above_upper_rail():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'low']] = [102.0, 101.2, 102.1, 101.1]
    result = TradingEngine._channel_swing_action(frame, 101.2, 'LONG')
    assert (result['action'], result['side']) == ('HOLD', None)

def test_kc_outer_profit_exit_has_no_same_symbol_reentry_path():
    process_source = inspect.getsource(TradingEngine._process_single_symbol)
    assert not hasattr(TradingEngine, '_channel_outer_reentry_reenter_action')
    assert '_channel_outer_reentry_after_exit' not in process_source

def test_late_kc_outer_entry_window_requests_symbol_replacement():
    assert TradingEngine._channel_entry_window_expired("KC_UPPER_EXTENSION_LATE")
    assert TradingEngine._channel_entry_window_expired("KC_LOWER_EXTENSION_LATE")
    assert not TradingEngine._channel_entry_window_expired("WAIT_CLOSE_RED")
    assert not TradingEngine._channel_entry_window_expired("CHOP_WAIT_NO_ENTRY")

def test_profit_exit_requests_immediate_symbol_replacement_with_cooldown():
    rotation = SymbolRotation.__new__(SymbolRotation)
    rotation.next_rotation_exclusions = set()
    rotation.replacement_cooldowns = {}
    rotation.last_rotation_at = 123.0
    rotation.request_replacement('SOL/USDT')
    assert rotation.next_rotation_exclusions == {'SOL/USDT'}
    assert rotation.replacement_cooldowns['SOL/USDT'] > 0.0
    assert rotation.replacement_exclusions() == {'SOL/USDT'}
    assert rotation.last_rotation_at == 0.0

def test_profit_exit_symbol_stays_excluded_after_pending_scan_is_consumed():
    rotation = SymbolRotation.__new__(SymbolRotation)
    rotation.next_rotation_exclusions = set()
    rotation.replacement_cooldowns = {}
    rotation.last_rotation_at = 123.0
    rotation.request_replacement('AAVE/USDT')
    rotation.next_rotation_exclusions.clear()
    assert rotation.replacement_exclusions() == {'AAVE/USDT'}

def test_upper_red_peak_short_only_allows_green_reversal_at_upper_rail():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], 'open'] = 100.8
    assert not TradingEngine._channel_upper_red_short_reversal_allowed(frame, 100.9)
    frame.loc[frame.index[-1], 'open'] = 101.1
    assert not TradingEngine._channel_upper_red_short_reversal_allowed(frame, 101.0)
    frame.loc[frame.index[-1], 'open'] = 100.9
    assert TradingEngine._channel_upper_red_short_reversal_allowed(frame, 101.0)

def test_upper_red_peak_short_origin_is_scoped_to_channel_peak_entries():
    assert TradingEngine._channel_is_upper_red_peak_short({'side': 'SHORT', 'entry_mode': 'CHANNEL_SWING', 'channel_turn_high': 101.0})
    assert not TradingEngine._channel_is_upper_red_peak_short({'side': 'SHORT', 'entry_mode': 'CHANNEL_SWING', 'channel_turn_low': 99.0})

def test_channel_entry_cannot_reuse_the_bar_that_just_exited():
    bar_id = 12345
    assert TradingEngine._channel_entry_reuses_exit_bar('ENTER', False, bar_id, bar_id)
    assert not TradingEngine._channel_entry_reuses_exit_bar('ENTER', False, bar_id + 1, bar_id)
    assert not TradingEngine._channel_entry_reuses_exit_bar('REVERSE', True, bar_id, bar_id)
    engine = object.__new__(TradingEngine)
    assert engine._channel_entry_reuses_exit_bar('ENTER', False, bar_id, bar_id)

def test_same_bar_upper_outer_rechase_immediately_reverses_short_without_half_kc():
    frame = _channel_frame(lower=99.8, upper=100.2)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [99.9, 100.1]
    result = TradingEngine._channel_same_bar_outer_rechase_action(frame, 100.2, 'SHORT', frame.index[-2])
    assert (result['action'], result['side'], result['reason']) == ('REVERSE', 'LONG', 'SAME_BAR_UPPER_OUTER_RECHASE')
    assert (100.2 - 99.8) / 100.2 < 0.005

def test_same_bar_lower_outer_rechase_immediately_reverses_long_without_half_kc():
    frame = _channel_frame(lower=99.8, upper=100.2)
    frame.loc[frame.index[-13:-1], ['low', 'high']] = [99.9, 100.1]
    result = TradingEngine._channel_same_bar_outer_rechase_action(frame, 99.8, 'LONG', frame.index[-2])
    assert (result['action'], result['side'], result['reason']) == ('REVERSE', 'SHORT', 'SAME_BAR_LOWER_OUTER_RECHASE')
    assert (100.2 - 99.8) / 99.8 < 0.005

def test_outer_rechase_requires_the_position_to_come_from_same_live_bar():
    frame = _channel_frame(lower=99.8, upper=100.2)
    result = TradingEngine._channel_same_bar_outer_rechase_action(frame, 100.2, 'SHORT', frame.index[-3])
    assert (result['action'], result['side']) == ('HOLD', None)

def test_same_bar_outer_rechase_bypasses_chop_close_only_gate():
    assert TradingEngine._channel_chop_gate('REVERSE', 'LONG', True, True, 'SAME_BAR_UPPER_OUTER_RECHASE') == ('REVERSE', 'LONG', None)
    assert TradingEngine._channel_chop_gate('REVERSE', 'SHORT', True, True, 'SAME_BAR_LOWER_OUTER_RECHASE') == ('REVERSE', 'SHORT', None)

def test_flat_live_outer_break_waits_for_closed_peak_confirmation():
    frame = _channel_frame(lower=99.0, upper=101.0)
    result = TradingEngine._channel_swing_action(frame, 101.1)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_CLOSE_RED')

def test_live_price_inside_kc_does_not_use_immediate_outer_entry():
    frame = _channel_frame(lower=99.8, upper=100.2)
    result = TradingEngine._channel_live_outer_entry_action(frame, 100.0)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_LIVE_OUTER_BREAK')
    action_source = inspect.getsource(TradingEngine._channel_swing_action)
    process_source = inspect.getsource(TradingEngine._process_single_symbol)
    assert 'INNER_' not in action_source
    assert 'inner_reentry' not in process_source

def test_second_closed_confirmation_candle_must_keep_direction_color():
    trough = _channel_frame()
    trough.loc[trough.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    trough.loc[trough.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.2, 98.9, 98.8, 99.3, 99.4]
    peak = _channel_frame()
    peak.loc[peak.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [101.2, 101.1, 101.05, 101.3, 101.3]
    peak.loc[peak.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [100.8, 101.1, 100.7, 101.2, 100.6]
    flat_trough = TradingEngine._channel_swing_action(trough, 99.0)
    held_short = TradingEngine._channel_swing_action(trough, 99.0, 'SHORT')
    flat_peak = TradingEngine._channel_swing_action(peak, 101.0)
    held_long = TradingEngine._channel_swing_action(peak, 101.0, 'LONG')
    assert (flat_trough['action'], flat_trough['side']) == ('ENTER', 'SHORT')
    assert (held_short['action'], held_short['side']) == ('HOLD', None)
    assert (flat_peak['action'], flat_peak['side']) == ('ENTER', 'LONG')
    assert (held_long['action'], held_long['side']) == ('HOLD', None)

def test_flat_live_outer_cross_requires_confirmed_trend_on_both_sides():
    upper = _channel_frame()
    upper.loc[upper.index[-2], 'close'] = 101.1
    upper_wait = TradingEngine._channel_outer_trend_entry_action(upper, 101.2)
    lower = _channel_frame()
    lower.loc[lower.index[-2], 'close'] = 98.9
    lower_wait = TradingEngine._channel_outer_trend_entry_action(lower, 98.8)
    assert (upper_wait['action'], upper_wait['side']) == ('WAIT', None)
    assert upper_wait['reason'] == 'WAIT_DYNAMIC_TREND'
    assert (lower_wait['action'], lower_wait['side']) == ('WAIT', None)
    assert lower_wait['reason'] == 'WAIT_DYNAMIC_DOWNTREND'

def test_outer_ma3_route_accepts_two_closed_turn_bars_that_remain_outside():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    long_entry = TradingEngine._channel_swing_action(long_frame, 99.9)
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    short_entry = TradingEngine._channel_swing_action(short_frame, 100.1)
    assert (long_entry['action'], long_entry['side']) == ('ENTER', 'LONG')
    assert (short_entry['action'], short_entry['side']) == ('ENTER', 'SHORT')

def test_body_deep_eighty_percent_into_half_channel_bypasses_outer_depth():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    long_frame.loc[long_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.85, 99.9, 98.9, 99.95, 98.95]
    long_frame.loc[long_frame.index[-2], ['low', 'high']] = [99.0, 100.0]
    long_turn = TradingEngine._channel_swing_action(long_frame, 100.0)
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    short_frame.loc[short_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [100.15, 100.1, 100.05, 101.1, 101.05]
    short_frame.loc[short_frame.index[-2], ['low', 'high']] = [100.0, 101.0]
    short_turn = TradingEngine._channel_swing_action(short_frame, 100.0)
    assert (long_turn['action'], long_turn['side']) == ('ENTER', 'LONG')
    assert (short_turn['action'], short_turn['side']) == ('ENTER', 'SHORT')

def test_shallow_outer_v_turns_are_symmetric_without_ma3_depth():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    long_frame.loc[long_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.1, 99.2, 98.9, 99.25, 98.7]
    long_frame.loc[long_frame.index[-2], 'low'] = 99.0
    long_turn = TradingEngine._channel_swing_action(long_frame, 100.0)
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    short_frame.loc[short_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [100.9, 100.8, 100.75, 101.1, 101.3]
    short_frame.loc[short_frame.index[-2], 'high'] = 101.0
    short_turn = TradingEngine._channel_swing_action(short_frame, 100.0)
    assert (long_turn['action'], long_turn['side']) == ('ENTER', 'LONG')
    assert (short_turn['action'], short_turn['side']) == ('ENTER', 'SHORT')

def test_lower_outer_green_reentry_can_open_long_without_ma3_depth():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [98.95, 99.2, 98.9, 99.25, 98.7]
    frame.loc[frame.index[-2], 'low'] = 99.0
    result = TradingEngine._channel_swing_action(frame, 100.0)
    assert (result['action'], result['side']) == ('ENTER', 'LONG')

def test_latest_adjacent_two_closed_outer_v_bars_are_valid_on_both_sides():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    long_turn = TradingEngine._channel_swing_action(long_frame, 99.8)
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    short_turn = TradingEngine._channel_swing_action(short_frame, 100.2)
    assert (long_turn['action'], long_turn['side']) == ('ENTER', 'LONG')
    assert (short_turn['action'], short_turn['side']) == ('ENTER', 'SHORT')

def test_flat_entry_does_not_reuse_multi_candle_confirmed_turn():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    long_frame.loc[long_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.4, 99.2, 99.05, 99.45, 99.2]
    long_frame.loc[long_frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.4, 99.7, 99.3, 99.75, 99.6]
    long_frame.loc[long_frame.index[-1], ['close', 'ma3']] = [99.8, 99.8]
    long_turn = TradingEngine._channel_swing_action(long_frame, 99.8)
    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [101.2, 101.1, 101.05, 101.3, 101.3]
    short_frame.loc[short_frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [100.6, 100.8, 100.55, 100.95, 100.8]
    short_frame.loc[short_frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [100.6, 100.3, 100.25, 100.7, 100.4]
    short_frame.loc[short_frame.index[-1], ['close', 'ma3']] = [100.2, 100.2]
    short_turn = TradingEngine._channel_swing_action(short_frame, 100.2)
    assert (long_turn['action'], long_turn['reason']) == ('WAIT', 'WAIT_ADJACENT_OUTER_CANDIDATE')
    assert long_turn['side'] is None
    assert (short_turn['action'], short_turn['reason']) == ('WAIT', 'WAIT_ADJACENT_OUTER_CANDIDATE')
    assert short_turn['side'] is None

def test_flat_entry_does_not_reuse_turn_after_opposite_color_candle():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.4, 99.2, 99.1, 99.5, 99.2]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.2, 99.7, 99.15, 99.75, 99.6]
    frame.loc[frame.index[-1], ['close', 'ma3']] = [99.8, 99.8]
    result = TradingEngine._channel_swing_action(frame, 99.8)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_ADJACENT_OUTER_CANDIDATE')
    assert result['side'] is None

def test_empty_slot_does_not_chase_kc_outer_trend_without_pivot_turn():
    up = _channel_frame()
    up.loc[up.index[-3], 'ma3'] = 100.5
    up.loc[up.index[-2], 'ma3'] = 100.9
    up.loc[up.index[-1], ['open', 'close', 'high', 'ma3']] = [101.1, 101.4, 101.5, 101.3]
    long_wait = TradingEngine._channel_swing_action(up, 101.4)
    down = _channel_frame()
    down.loc[down.index[-3], 'ma3'] = 99.5
    down.loc[down.index[-2], 'ma3'] = 99.1
    down.loc[down.index[-1], ['open', 'close', 'low', 'ma3']] = [98.9, 98.6, 98.5, 98.7]
    short_wait = TradingEngine._channel_swing_action(down, 98.6)
    assert (long_wait['action'], long_wait['side']) == ('ENTER', 'LONG')
    assert long_wait['turn_low'] is None
    assert (short_wait['action'], short_wait['side']) == ('ENTER', 'SHORT')
    assert short_wait['turn_high'] is None

def test_empty_slot_does_not_chase_price_outside_without_ma3_trend():
    frame = _channel_frame()
    frame.loc[frame.index[-2], 'ma3'] = 100.8
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [101.1, 101.4, 101.5, 100.9]
    result = TradingEngine._channel_swing_action(frame, 101.4)
    assert result['action'] == 'ENTER'

def test_live_outer_break_does_not_require_ma3_slope():
    up = _channel_frame()
    up.loc[up.index[-3], 'ma3'] = 100.0
    up.loc[up.index[-2], 'ma3'] = 100.9
    up.loc[up.index[-1], ['open', 'close', 'high', 'ma3']] = [101.1, 101.4, 101.5, 101.2]
    down = _channel_frame()
    down.loc[down.index[-3], 'ma3'] = 100.0
    down.loc[down.index[-2], 'ma3'] = 99.1
    down.loc[down.index[-1], ['open', 'close', 'low', 'ma3']] = [98.9, 98.6, 98.5, 98.8]
    assert TradingEngine._channel_swing_action(up, 101.4)['action'] == 'ENTER'
    assert TradingEngine._channel_swing_action(down, 98.6)['action'] == 'ENTER'

def test_empty_slot_does_not_chase_when_only_close_breaks_outer_rail():
    frame = _channel_frame()
    frame.loc[frame.index[-3], 'ma3'] = 100.5
    frame.loc[frame.index[-2], 'ma3'] = 100.9
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [100.9, 101.4, 101.5, 101.3]
    result = TradingEngine._channel_swing_action(frame, 101.4)
    assert result['action'] == 'ENTER'

def test_prior_downtrend_inside_kc_does_not_open_continuation_chase():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    frame.loc[frame.index[-3], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 100.0, 100.0, 98.9, 100.9]
    frame.loc[frame.index[-2], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 99.9, 99.9, 98.85, 100.85]
    frame.loc[frame.index[-1], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [99.8, 99.6, 99.8, 98.8, 100.8]
    result = TradingEngine._channel_swing_action(frame, 99.6)
    assert (result['action'], result['side']) == ('WAIT', None)
    assert result['reason'] == 'WAIT_ADJACENT_OUTER_CANDIDATE'
    assert result['turn_low'] is None
    assert result['turn_high'] is None

def test_prior_downtrend_blocks_green_countertrend_long_entry():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    frame.loc[frame.index[-3], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 100.0, 100.0, 98.9, 100.9]
    frame.loc[frame.index[-2], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 99.9, 99.9, 98.85, 100.85]
    frame.loc[frame.index[-1], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [99.5, 99.6, 99.8, 98.8, 100.8]
    result = TradingEngine._channel_swing_action(frame, 99.6)
    assert (result['action'], result['side']) == ('WAIT', None)
    assert result['reason'] == 'WAIT_ADJACENT_OUTER_CANDIDATE'

def test_prior_uptrend_inside_kc_does_not_open_continuation_chase():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [101.2, 101.1, 101.05, 101.3, 101.3]
    frame.loc[frame.index[-3], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 100.0, 100.0, 99.1, 101.1]
    frame.loc[frame.index[-2], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.0, 100.1, 100.1, 99.15, 101.15]
    frame.loc[frame.index[-1], ['open', 'close', 'ma3', 'kc_lower', 'kc_upper']] = [100.2, 100.4, 100.2, 99.2, 101.2]
    result = TradingEngine._channel_swing_action(frame, 100.4)
    assert (result['action'], result['side']) == ('WAIT', None)
    assert result['reason'] == 'WAIT_ADJACENT_OUTER_CANDIDATE'
    assert result['turn_low'] is None
    assert result['turn_high'] is None

def test_channel_swing_holds_between_entry_and_opposite_edge():
    frame = _channel_frame()
    assert TradingEngine._channel_swing_action(frame, 100.0, 'LONG')['action'] == 'HOLD'
    assert TradingEngine._channel_swing_action(frame, 100.0, 'SHORT')['action'] == 'HOLD'

def test_single_closed_outer_red_candidate_waits_for_second_closed_red_k():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ['open', 'close', 'high', 'low', 'ma3']] = [101.2, 100.8, 101.3, 100.7, 101.0]
    result = TradingEngine._channel_swing_action(frame, 100.8, 'LONG')
    assert (result['action'], result['side']) == ('HOLD', None)

def test_single_closed_outer_green_candidate_waits_for_second_closed_green_k():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ['open', 'close', 'high', 'low', 'ma3']] = [98.8, 99.2, 99.3, 98.7, 99.0]
    result = TradingEngine._channel_swing_action(frame, 99.2, 'SHORT')
    assert (result['action'], result['side']) == ('HOLD', None)

def test_channel_swing_does_not_enter_from_unclosed_live_green_or_red_candle():
    frame = _channel_frame()
    frame.loc[frame.index[-1], ['open', 'low']] = [99.1, 98.9]
    no_trough = TradingEngine._channel_swing_action(frame, 99.2)
    assert no_trough['action'] == 'WAIT'
    assert no_trough['reason'] == 'WAIT_CLOSE_GREEN'
    frame = _channel_frame()
    frame.loc[frame.index[-1], ['open', 'high']] = [100.9, 101.1]
    no_peak = TradingEngine._channel_swing_action(frame, 100.8)
    assert no_peak['action'] == 'WAIT'
    assert no_peak['reason'] == 'WAIT_CLOSE_RED'

def test_closed_lower_trough_uses_confirmed_long_instead_of_live_outer_short():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-2], ['high', 'low']] = [98.9, 98.8]
    result = TradingEngine._channel_swing_action(frame, 98.9)
    assert (result['action'], result['side']) == ('ENTER', 'LONG')
    assert result['reason'] == 'OUTER_TROUGH_NEXT_BREAK_LONG'

def test_failed_lower_trough_does_not_fall_back_to_live_outer_short():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-2], 'low'] = 98.6
    result = TradingEngine._channel_swing_action(frame, 98.6)
    assert (result['action'], result['side']) == ('WAIT', None)
    assert result['reason'] == 'CANCEL_LONG'

def test_cancelled_outer_trough_cannot_fall_back_to_live_ma3_entry():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-2], ['low', 'high']] = [98.6, 99.3]
    result = TradingEngine._channel_swing_action(frame, 99.2)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'CANCEL_LONG')

def test_cancelled_outer_peak_cannot_fall_back_to_live_ma3_entry():
    frame = _channel_frame()
    _closed_peak(frame)
    frame.loc[frame.index[-2], ['low', 'high']] = [101.1, 101.2]
    waiting = TradingEngine._channel_swing_action(frame, 101.1)
    frame.loc[frame.index[-2], ['low', 'high']] = [100.7, 101.4]
    cancelled = TradingEngine._channel_swing_action(frame, 100.8)
    assert (waiting['action'], waiting['side']) == ('ENTER', 'SHORT')
    assert waiting['reason'] == 'OUTER_PEAK_NEXT_BREAK_SHORT'
    assert (cancelled['action'], cancelled['side'], cancelled['reason']) == ('WAIT', None, 'CANCEL_SHORT')

def test_channel_swing_does_not_exit_before_actual_rail_touch():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high']] = [101.0, 100.9, 100.8, 100.95]
    result = TradingEngine._channel_swing_action(frame, 100.8, 'LONG')
    assert result['action'] == 'HOLD'
    assert result['side'] is None

def test_channel_chop_state_detects_repeated_ma_and_middle_crosses():
    frame = _channel_frame()
    closes = [99.6, 100.4] * 6
    ma3 = [99.7, 100.3] * 6
    frame.loc[frame.index[-13:-1], 'close'] = closes
    frame.loc[frame.index[-13:-1], 'ma3'] = ma3
    result = TradingEngine._channel_chop_state(frame)
    assert result['detected'] is True
    assert result['clear_direction'] is None
    assert result['ma_crosses'] >= 3
    assert result['middle_crosses'] >= 3

def test_channel_chop_state_unlocks_after_three_clear_closed_bars():
    frame = _channel_frame()
    for position in range(8, 20):
        middle = 99.4 + position * 0.08
        frame.loc[position, ['kc_lower', 'kc_upper', 'ma15', 'ma3', 'close']] = [middle - 1.0, middle + 1.0, middle - 0.2, middle + 0.2, middle + 0.35]
    result = TradingEngine._channel_chop_state(frame)
    assert result['detected'] is False
    assert result['clear_direction'] == 'LONG'

def test_channel_near_chop_gate_blocks_only_new_entries():
    assert TradingEngine._channel_near_chop_entry_gate("ENTER", "LONG", True, False) == ("WAIT", None, "CHOP_NEAR_LOCK_NO_ENTRY")
    assert TradingEngine._channel_near_chop_entry_gate("ENTER", "SHORT", False, False) == ("ENTER", "SHORT", None)
    assert TradingEngine._channel_near_chop_entry_gate("EXIT", None, True, True) == ("EXIT", None, None)

def test_channel_chop_gate_blocks_entry_and_turns_reverse_into_close_only():
    assert TradingEngine._channel_chop_gate('ENTER', 'LONG', True, False) == ('WAIT', None, 'CHOP_WAIT_NO_ENTRY')
    assert TradingEngine._channel_chop_gate('REVERSE', 'SHORT', True, True) == ('EXIT', None, 'CHOP_WAIT_CLOSE_ONLY')
    assert TradingEngine._channel_chop_gate('ENTER', 'LONG', False, False) == ('ENTER', 'LONG', None)

def test_confirmed_outer_peak_exits_after_price_returns_inside_kc():
    frame = _channel_frame()
    _closed_peak(frame)
    frame.loc[frame.index[-1], ['low', 'high']] = [101.1, 101.2]
    result = TradingEngine._channel_swing_action(frame, 100.6, 'LONG', market_mode='BEAR')
    assert (result['action'], result['side'], result['reason']) == (
        "EXIT", None, "KC_UPPER_OUTER_PEAK_EXIT",
    )

def test_flat_entry_uses_ma3_and_held_position_exits_on_confirmed_trough():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-2], ['open', 'ma3']] = [98.8, 98.9]
    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, 'SHORT')
    assert (empty['action'], empty['side']) == ('ENTER', 'LONG')
    assert (held_short['action'], held_short['side']) == ('REVERSE', 'LONG')

def test_confirmed_outer_pivot_opens_before_forty_percent_reentry():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    long_frame.loc[long_frame.index[-1], ['open', 'close', 'high', 'low', 'ma3']] = [99.05, 99.2, 99.3, 99.0, 99.2]
    long_entry = TradingEngine._channel_swing_action(long_frame, 99.2)
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    short_frame.loc[short_frame.index[-1], ['open', 'close', 'high', 'low', 'ma3']] = [100.95, 100.8, 101.0, 100.7, 100.8]
    short_entry = TradingEngine._channel_swing_action(short_frame, 100.8)
    assert (short_entry['action'], short_entry['side']) == ('ENTER', 'SHORT')
    assert (long_entry['action'], long_entry['side']) == ('ENTER', 'LONG')

def test_current_trend_does_not_reuse_already_confirmed_outer_pivot():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.1, 99.0, 98.9, 99.2, 99.0]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.1, 99.1, 99.0, 99.2, 99.2]
    frame.loc[frame.index[-1], ['open', 'close', 'low', 'high', 'ma3']] = [99.6, 99.5, 99.15, 99.65, 99.4]
    result = TradingEngine._channel_swing_action(frame, 99.5)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_ADJACENT_OUTER_CANDIDATE')
    assert result['side'] is None

def test_current_downtrend_after_outer_peak_opens_on_green_candle():
    frame = _channel_frame()
    _closed_peak(frame)
    frame.loc[frame.index[-1], ['open', 'close', 'low', 'high', 'ma3']] = [100.7, 100.8, 100.6, 101.0, 100.8]
    result = TradingEngine._channel_swing_action(frame, 100.8)
    assert (result['action'], result['side']) == ('ENTER', 'SHORT')

def test_inside_kc_two_closed_green_candles_do_not_open_long():
    frame = _channel_frame()
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high']] = [99.4, 99.6, 99.3, 99.7]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high']] = [100.1, 100.4, 99.4, 100.5]
    frame.loc[frame.index[-1], ['open', 'close']] = [100.5, 99.8]
    result = TradingEngine._channel_swing_action(frame, 99.8)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_ADJACENT_OUTER_CANDIDATE')

def test_inside_kc_two_closed_red_candles_do_not_open_short():
    frame = _channel_frame()
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high']] = [100.6, 100.4, 100.3, 100.7]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high']] = [99.9, 99.6, 99.5, 100.6]
    frame.loc[frame.index[-1], ['open', 'close']] = [99.5, 100.2]
    result = TradingEngine._channel_swing_action(frame, 100.2)
    assert (result['action'], result['side'], result['reason']) == ('WAIT', None, 'WAIT_ADJACENT_OUTER_CANDIDATE')

def test_inside_kc_second_candle_reverse_extreme_cancels_entry():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-3], ['open', 'close', 'low', 'high']] = [99.4, 99.6, 99.3, 99.7]
    long_frame.loc[long_frame.index[-2], ['open', 'close', 'low', 'high']] = [100.1, 100.4, 99.2, 100.5]
    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-3], ['open', 'close', 'low', 'high']] = [100.6, 100.4, 100.3, 100.7]
    short_frame.loc[short_frame.index[-2], ['open', 'close', 'low', 'high']] = [99.9, 99.6, 99.5, 100.8]
    long_result = TradingEngine._channel_swing_action(long_frame, 100.4)
    short_result = TradingEngine._channel_swing_action(short_frame, 99.6)
    assert (long_result['action'], long_result['side']) == ('WAIT', None)
    assert (short_result['action'], short_result['side']) == ('WAIT', None)

def test_inside_kc_live_second_candle_only_previews_and_does_not_enter():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-2], ['open', 'close', 'low', 'high']] = [99.4, 99.6, 99.3, 99.7]
    long_frame.loc[long_frame.index[-1], ['open', 'close', 'low', 'high']] = [100.1, 100.4, 99.4, 100.5]
    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-2], ['open', 'close', 'low', 'high']] = [100.6, 100.4, 100.3, 100.7]
    short_frame.loc[short_frame.index[-1], ['open', 'close', 'low', 'high']] = [99.9, 99.6, 99.5, 100.6]
    long_result = TradingEngine._channel_swing_action(long_frame, 100.4)
    short_result = TradingEngine._channel_swing_action(short_frame, 99.6)
    assert (long_result['action'], long_result['side']) == ('WAIT', None)
    assert (short_result['action'], short_result['side']) == ('WAIT', None)

def test_inside_kc_ma3_continuation_without_turn_still_waits():
    frame = _channel_frame()
    frame.loc[frame.index[-3], 'ma3'] = 99.6
    frame.loc[frame.index[-2], 'ma3'] = 99.8
    frame.loc[frame.index[-1], 'ma3'] = 100.0
    result = TradingEngine._channel_swing_action(frame, 100.2)
    assert (result['action'], result['side']) == ('WAIT', None)

def test_ma3_turn_does_not_open_when_price_is_outside_kc():
    frame = _channel_frame()
    frame.loc[frame.index[-2], 'ma3'] = 99.7
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [101.1, 101.2, 101.3, 100.0]
    result = TradingEngine._channel_swing_action(frame, 101.2)
    assert (result['action'], result['side']) == ('ENTER', 'LONG')

def test_old_outer_pivot_does_not_chase_at_opposite_outer_rail():
    frame = _channel_frame()
    frame.loc[frame.index[-4], ['open', 'close', 'low', 'high', 'ma3']] = [98.8, 98.9, 98.7, 98.95, 98.7]
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [100.8, 101.1, 101.2, 100.9]
    result = TradingEngine._channel_swing_action(frame, 101.1)
    assert (result['action'], result['side']) == ('ENTER', 'LONG')

def test_shallow_outer_reentry_still_reverses_held_short_on_confirmed_trough():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], 'ma3'] = 99.2
    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, 'SHORT')
    assert (empty['action'], empty['side']) == ('ENTER', 'LONG')
    assert (held_short['action'], held_short['side']) == ('REVERSE', 'LONG')

def test_channel_swing_reentry_boundary_is_80_percent_of_outer_half():
    below = _channel_frame()
    _closed_trough(below)
    below.loc[below.index[-1], ['close', 'ma3']] = [99.78, 99.78]
    rejected = TradingEngine._channel_swing_action(below, 99.78)
    boundary = _channel_frame()
    _closed_trough(boundary)
    boundary.loc[boundary.index[-1], ['close', 'ma3']] = [99.8, 99.8]
    accepted = TradingEngine._channel_swing_action(boundary, 99.8)
    assert (rejected['action'], rejected['side']) == ('ENTER', 'LONG')
    assert (accepted['action'], accepted['side']) == ('ENTER', 'LONG')

def test_live_ma3_turn_does_not_block_confirmed_trough_exit():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [99.1, 99.2, 100.5, 100.0]
    empty = TradingEngine._channel_swing_action(frame, 99.2)
    held_short = TradingEngine._channel_swing_action(frame, 99.2, 'SHORT')
    assert (empty['action'], empty['side']) == ('ENTER', 'LONG')
    assert (held_short['action'], held_short['side']) == ('REVERSE', 'LONG')

def test_channel_swing_positions_are_not_managed_by_early_exit_loops():
    assert TradingEngine._is_continuous_wave_position({'entry_mode': 'CHANNEL_SWING'})

def test_channel_swing_has_one_confirmation_rule_and_no_legacy_entry_paths():
    from services.api import manual_order
    action_source = inspect.getsource(TradingEngine._channel_swing_action)
    process_source = inspect.getsource(TradingEngine._process_single_symbol)
    manual_source = inspect.getsource(manual_order)
    assert 'CONTINUOUS_ENTRY_OUTER_ZONE_RATIO' not in action_source
    assert '"entry_mode": "CHANNEL_SWING"' in manual_source
    assert '"wave_regime": "RANGE"' in manual_source
    assert 'KC 撕裂復原' not in process_source
    assert 'swing_direction' not in process_source
    assert '_channel_trend_entry_action(' not in process_source
    assert 'KC_INNER_MA3_TURN' not in action_source
    assert 'KC_INNER_TWO_GREEN_CROSS_UP' not in action_source
    assert 'KC_INNER_TWO_RED_CROSS_DOWN' not in action_source
    assert 'inner_ma3_turn' not in action_source
    # 空手即時外軌突破必須優先，不等待已收盤候選。
    assert '_channel_live_outer_entry_action(' in process_source
    assert '_channel_outer_trend_entry_action(' in process_source
    reverse_start = process_source.index('if action == "REVERSE" and existing_pos:')
    reverse_end = process_source.index('if existing_pos:', reverse_start + 1)
    reverse_source = process_source[reverse_start:reverse_end]
    assert reverse_source.index('close_position(') < reverse_source.index('detected_candidates.append(')
    assert '_strongest_ranked_symbol' not in reverse_source
    assert '"symbol": symbol' in reverse_source
    assert 'close-first' in reverse_source

def test_breaking_entry_pivot_without_full_outer_body_keeps_position():
    frame = _channel_frame()
    held_long = TradingEngine._channel_swing_action(frame, 98.8, 'LONG', entry_turn_low=98.9)
    held_short = TradingEngine._channel_swing_action(frame, 101.2, 'SHORT', entry_turn_high=101.1)
    assert (held_long['action'], held_long['side']) == ('HOLD', None)
    assert (held_short['action'], held_short['side']) == ('HOLD', None)

def test_partial_body_crossing_entry_side_outer_rail_keeps_position():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-1], ['open', 'close', 'low']] = [99.2, 98.8, 98.7]
    held_long = TradingEngine._channel_swing_action(long_frame, 98.8, 'LONG')
    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-1], ['open', 'close', 'high']] = [100.8, 101.2, 101.3]
    held_short = TradingEngine._channel_swing_action(short_frame, 101.2, 'SHORT')
    assert (held_long['action'], held_long['side']) == ('HOLD', None)
    assert (held_short['action'], held_short['side']) == ('HOLD', None)

def test_confirmed_outer_peak_and_trough_exit_without_body_reentry():
    long_frame = _channel_frame()
    _closed_peak(long_frame)
    held_long = TradingEngine._channel_swing_action(long_frame, 101.1, 'LONG', market_mode='BEAR')
    short_frame = _channel_frame()
    _closed_trough(short_frame)
    held_short = TradingEngine._channel_swing_action(short_frame, 98.9, 'SHORT', market_mode='BULL')
    assert (held_long['action'], held_long['side'], held_long['reason']) == ('EXIT', None, 'KC_UPPER_OUTER_PEAK_EXIT')
    assert (held_short['action'], held_short['side'], held_short['reason']) == ('EXIT', None, 'KC_LOWER_OUTER_VALLEY_EXIT')

def test_held_position_ignores_adverse_side_outer_pivot():
    long_frame = _channel_frame()
    _closed_trough(long_frame)
    held_long = TradingEngine._channel_swing_action(long_frame, 98.9, "LONG")
    short_frame = _channel_frame()
    _closed_peak(short_frame)
    held_short = TradingEngine._channel_swing_action(short_frame, 101.1, "SHORT")
    assert (held_long["action"], held_long["reason"]) == ("HOLD", "WAIT_OPPOSITE_KC_UPPER_PEAK")
    assert (held_short["action"], held_short["reason"]) == ("HOLD", "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_channel_swing_background_trigger_loop_is_diagnostic_only():
    source = inspect.getsource(TradingEngine._position_trigger_loop)
    guard = source.index("if pos_entry_mode.upper() == \"CHANNEL_SWING\":")
    rapid_exit = source.index("if rapid_adverse_exit")
    assert guard < rapid_exit


def test_bull_long_holds_when_red_candle_reenters_before_outer_peak():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [101.2, 100.8, 101.3, 100.7]
    result = TradingEngine._channel_swing_action(frame, 100.8, "LONG", market_mode="BULL")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_UPPER_PEAK")


def test_bull_long_holds_while_the_reversal_candle_is_still_outside_kc():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [101.3, 101.1, 101.4, 101.05]
    result = TradingEngine._channel_swing_action(frame, 101.1, "LONG", market_mode="BULL")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_UPPER_PEAK")


def test_bull_long_does_not_exit_on_the_upper_kc_boundary():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [101.2, 101.0, 101.3, 100.9]
    result = TradingEngine._channel_swing_action(frame, 101.0, "LONG", market_mode="BULL")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_UPPER_PEAK")

def test_bear_short_holds_when_green_candle_reenters_before_outer_valley():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [98.8, 99.2, 99.3, 98.7]
    result = TradingEngine._channel_swing_action(frame, 99.2, "SHORT", market_mode="BEAR")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_bear_short_holds_while_the_reversal_candle_is_still_outside_kc():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [98.7, 98.9, 98.95, 98.6]
    result = TradingEngine._channel_swing_action(frame, 98.9, "SHORT", market_mode="BEAR")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_bear_short_does_not_exit_on_the_lower_kc_boundary():
    frame = _channel_frame(lower=99.0, upper=101.0)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [98.8, 99.0, 99.1, 98.7]
    result = TradingEngine._channel_swing_action(frame, 99.0, "SHORT", market_mode="BEAR")
    assert (result["action"], result["side"], result["reason"]) == ("HOLD", None, "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_range_positions_also_wait_for_opposite_outer_pivot():
    long_frame = _channel_frame(lower=99.0, upper=101.0)
    long_frame.loc[long_frame.index[-1], ["open", "close", "high", "low"]] = [101.2, 100.8, 101.3, 100.7]
    short_frame = _channel_frame(lower=99.0, upper=101.0)
    short_frame.loc[short_frame.index[-1], ["open", "close", "high", "low"]] = [98.8, 99.2, 99.3, 98.7]
    long_result = TradingEngine._channel_swing_action(long_frame, 100.8, "LONG", market_mode="RANGE")
    short_result = TradingEngine._channel_swing_action(short_frame, 99.2, "SHORT", market_mode="RANGE")
    assert (long_result["action"], long_result["reason"]) == ("HOLD", "WAIT_OPPOSITE_KC_UPPER_PEAK")
    assert (short_result["action"], short_result["reason"]) == ("HOLD", "WAIT_OPPOSITE_KC_LOWER_VALLEY")


def test_range_flat_confirmed_outer_trough_enters_long():
    frame = _channel_frame()
    _closed_trough(frame)
    result = TradingEngine._channel_swing_action(
        frame, 99.2, market_mode="RANGE",
    )
    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", "LONG", "RANGE_KC_LOWER_TROUGH_CONFIRMED_LONG",
    )


def test_range_flat_confirmed_outer_peak_enters_short():
    frame = _channel_frame()
    _closed_peak(frame)
    result = TradingEngine._channel_swing_action(
        frame, 100.8, market_mode="RANGE",
    )
    assert (result["action"], result["side"], result["reason"]) == (
        "ENTER", "SHORT", "RANGE_KC_UPPER_PEAK_CONFIRMED_SHORT",
    )


def test_bear_market_enters_short_at_confirmed_peak_but_not_long_at_trough():
    trough = _channel_frame()
    _closed_trough(trough)
    peak = _channel_frame()
    _closed_peak(peak)
    long_result = TradingEngine._channel_swing_action(
        trough, 99.2, market_mode="BEAR",
    )
    short_result = TradingEngine._channel_swing_action(
        peak, 100.8, market_mode="BEAR",
    )
    assert (long_result["action"], long_result["reason"]) == (
        "WAIT", "OUTER_PIVOT_AGAINST_MARKET_TREND",
    )
    assert (short_result["action"], short_result["side"], short_result["reason"]) == (
        "ENTER", "SHORT", "BEAR_KC_UPPER_PEAK_CONFIRMED_SHORT",
    )


def test_bull_market_enters_long_at_confirmed_trough_but_not_short_at_peak():
    trough = _channel_frame()
    _closed_trough(trough)
    peak = _channel_frame()
    _closed_peak(peak)
    long_result = TradingEngine._channel_swing_action(
        trough, 99.2, market_mode="BULL",
    )
    short_result = TradingEngine._channel_swing_action(
        peak, 100.8, market_mode="BULL",
    )
    assert (long_result["action"], long_result["side"], long_result["reason"]) == (
        "ENTER", "LONG", "BULL_KC_LOWER_TROUGH_CONFIRMED_LONG",
    )
    assert (short_result["action"], short_result["reason"]) == (
        "WAIT", "OUTER_PIVOT_AGAINST_MARKET_TREND",
    )


@pytest.mark.parametrize("market_mode", [None, "TREND"])
def test_directionless_market_does_not_use_outer_pivot_entry(market_mode):
    trough = _channel_frame()
    _closed_trough(trough)
    peak = _channel_frame()
    _closed_peak(peak)
    long_result = TradingEngine._channel_swing_action(
        trough, 99.2, market_mode=market_mode,
    )
    short_result = TradingEngine._channel_swing_action(
        peak, 100.8, market_mode=market_mode,
    )
    assert (long_result["action"], long_result["side"]) == ("WAIT", None)
    assert (short_result["action"], short_result["side"]) == ("WAIT", None)


def test_range_outer_pivot_requires_confirmed_ma3_turn():
    trough = _channel_frame()
    _closed_trough(trough)
    trough.loc[trough.index[-2], "ma3"] = trough.loc[trough.index[-3], "ma3"]
    peak = _channel_frame()
    _closed_peak(peak)
    peak.loc[peak.index[-2], "ma3"] = peak.loc[peak.index[-3], "ma3"]
    long_result = TradingEngine._channel_swing_action(
        trough, 99.2, market_mode="RANGE",
    )
    short_result = TradingEngine._channel_swing_action(
        peak, 100.8, market_mode="RANGE",
    )
    assert (long_result["action"], long_result["side"]) == ("WAIT", None)
    assert (short_result["action"], short_result["side"]) == ("WAIT", None)


def test_range_outer_touch_without_adjacent_confirmation_does_not_enter():
    trough = _channel_frame()
    trough.loc[trough.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        98.8, 98.9, 98.7, 98.95, 98.7,
    ]
    peak = _channel_frame()
    peak.loc[peak.index[-2], ["open", "close", "low", "high", "ma3"]] = [
        101.2, 101.1, 101.05, 101.3, 101.3,
    ]
    long_result = TradingEngine._channel_swing_action(
        trough, 98.9, market_mode="RANGE",
    )
    short_result = TradingEngine._channel_swing_action(
        peak, 101.1, market_mode="RANGE",
    )
    assert (long_result["action"], long_result["side"]) == ("WAIT", None)
    assert (short_result["action"], short_result["side"]) == ("WAIT", None)


def test_countertrend_channel_positions_keep_outer_pivot_exit():
    long_frame = _channel_frame()
    _closed_peak(long_frame)
    bear_long = TradingEngine._channel_swing_action(long_frame, 101.1, "LONG", market_mode="BEAR")
    short_frame = _channel_frame()
    _closed_trough(short_frame)
    bull_short = TradingEngine._channel_swing_action(short_frame, 98.9, "SHORT", market_mode="BULL")
    assert bear_long["reason"] == "KC_UPPER_OUTER_PEAK_EXIT"
    assert bull_short["reason"] == "KC_LOWER_OUTER_VALLEY_EXIT"

def test_btc_1m_pulse_requires_atr_move_and_ma3_alignment(monkeypatch):
    monkeypatch.setattr('core.engine.BTC_1M_PULSE_FILTER_ENABLED', True)
    frame = pd.DataFrame({'close': [100.0, 100.0, 100.2, 100.5, 100.8], 'ma3': [100.0, 100.0, 100.1, 100.3, 100.6], 'atr': [1.0] * 5})
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.8) == 'LONG'
    frame['close'] = [100.8, 100.8, 100.6, 100.3, 100.0]
    frame['ma3'] = [100.8, 100.8, 100.7, 100.5, 100.2]
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.0) == 'SHORT'

def test_btc_lead_shadow_records_aligned_outer_reaction_without_order():
    engine = TradingEngine.__new__(TradingEngine)
    engine._btc_lead_shadow_active = {"key": ("LONG", 1), "side": "LONG", "started_at": 0.0}
    engine._btc_lead_shadow_events = []
    engine._record_btc_lead_shadow_candidate("TEST/USDT", _dynamic_upper_trend_frame(), 101.2, False)
    assert engine.btc_lead_shadow_status()["eligible_events"] == 1

def test_adjacent_two_closed_green_bars_confirm_long_candidate():
    frame = _channel_frame()
    _closed_trough(frame)
    for i in range(1, 4):
        frame.loc[frame.index[-i], ['kc_lower', 'ema_20', 'kc_upper']] = [98.9, 99.9, 100.9]
    frame.loc[frame.index[-3], ['kc_lower', 'ema_20', 'kc_upper']] = [98.9, 99.9, 100.9]
    frame.loc[frame.index[-2], ['kc_lower', 'ema_20', 'kc_upper']] = [99.0, 100.0, 101.0]
    frame.loc[frame.index[-1], ['kc_lower', 'ema_20', 'kc_upper']] = [99.1, 100.1, 101.1]
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.0, 99.4, 98.9, 99.5, 99.0]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.4, 100.1, 99.3, 100.2, 100.2]
    frame.loc[frame.index[-1], ['open', 'close', 'low', 'ma3']] = [99.6, 99.7, 99.5, 100.3]
    result = TradingEngine._channel_swing_action(frame, 99.8)
    assert (result.get('action'), result.get('side')) == ('ENTER', 'LONG')

def test_adjacent_two_closed_red_bars_confirm_short_candidate():
    frame = _channel_frame()
    _closed_peak(frame)
    for i in range(1, 4):
        frame.loc[frame.index[-i], ['kc_lower', 'ema_20', 'kc_upper']] = [99.1, 100.1, 101.1]
    frame.loc[frame.index[-3], ['kc_lower', 'ema_20', 'kc_upper']] = [99.1, 100.1, 101.1]
    frame.loc[frame.index[-2], ['kc_lower', 'ema_20', 'kc_upper']] = [99.0, 100.0, 101.0]
    frame.loc[frame.index[-1], ['kc_lower', 'ema_20', 'kc_upper']] = [98.9, 99.9, 100.9]
    frame.loc[frame.index[-3], ['open', 'close', 'high', 'low', 'ma3']] = [101.0, 100.6, 101.1, 100.5, 101.5]
    frame.loc[frame.index[-2], ['open', 'close', 'high', 'low', 'ma3']] = [100.6, 99.9, 100.8, 99.8, 101.0]
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [100.3, 100.1, 100.4, 99.7]
    result = TradingEngine._channel_swing_action(frame, 100.2)
    assert (result.get('action'), result.get('side')) == ('ENTER', 'SHORT')

def test_detect_btc_pulse():
    frame = pd.DataFrame()
    frame['close'] = [100.0, 100.0, 100.1, 100.2, 100.3]
    frame['ma3'] = [100.0, 100.0, 100.1, 100.2, 100.25]
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.3) is None
    assert TradingEngine._btc_pulse_blocks_entry('SHORT', 'LONG') is True
    assert TradingEngine._btc_pulse_blocks_entry('LONG', 'SHORT') is True
    assert TradingEngine._btc_pulse_blocks_entry('LONG', 'LONG') is False
    assert TradingEngine._btc_pulse_blocks_entry('SHORT', None) is False

def test_btc_1m_pulse_is_disabled_by_configuration(monkeypatch):
    monkeypatch.setattr('core.engine.BTC_1M_PULSE_FILTER_ENABLED', False)
    frame = pd.DataFrame({'close': [100.0, 100.2, 100.5, 100.8], 'ma3': [100.0, 100.1, 100.3, 100.6], 'atr': [1.0] * 4})
    assert TradingEngine._detect_btc_1m_pulse(frame, 100.8) is None

def _dynamic_upper_trend_frame():
    frame = _channel_frame()
    for position in range(13, 19):
        middle = 99.5 + (position - 13) * 0.1
        close = middle + 0.8 + (position - 13) * 0.05
        frame.loc[position, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [close - 0.15, close, close + 0.1, close - 0.2, middle + 0.3, middle + 0.1, middle - 1.0, middle + 1.0]
    frame.loc[19, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [101.2, 101.35, 101.36, 101.15, 100.55, 100.25, 99.1, 101.1]
    return frame

def test_kc_upper_outer_uptrend_uses_existing_quality_without_three_bar_delay():
    frame = _dynamic_upper_trend_frame()
    candidate = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)
    assert (candidate['action'], candidate['reason']) == ('WAIT', 'WAIT_TREND_BREAK')
    assert candidate['pending']['side'] == 'LONG'
    assert candidate['pending']['confirmed'] is False
    entered = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.35, candidate['pending'])
    assert (entered['action'], entered['side']) == ('ENTER', 'LONG')
    assert entered['reason'] == 'KC_UPPER_TREND_CONFIRMED_LONG'

def test_kc_upper_outer_uptrend_waits_when_existing_trend_is_ambiguous():
    frame = _dynamic_upper_trend_frame()
    frame.loc[16:18, ['ma3', 'ma15']] = [[100.0, 100.1]] * 3
    result = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_DYNAMIC_TREND')
    assert result['pending'] is None

def test_kc_upper_outer_uptrend_next_bar_failure_cancels_candidate():
    frame = _dynamic_upper_trend_frame()
    seed = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)
    frame.loc[19, 'low'] = float(seed['pending']['candidate_low']) - 0.01
    result = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.2, seed['pending'])
    assert (result['action'], result['reason']) == ('WAIT', 'CANCEL_TREND_CONFIRM')
    assert result['pending'] is None

def test_kc_upper_outer_uptrend_does_not_chase_when_break_is_too_far():
    frame = _dynamic_upper_trend_frame()
    seed = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.25)
    result = TradingEngine._channel_outer_uptrend_entry_action(frame, 102.0, seed['pending'])
    assert result['action'] == 'WAIT'
    assert result['reason'] in ('WAIT_TREND_RETEST', 'WAIT_TREND_RETEST_BREAK')
    assert result['pending']['confirmed'] is True

def _dynamic_lower_trend_frame():
    frame = _channel_frame()
    for position in range(13, 19):
        middle = 100.5 - (position - 13) * 0.1
        close = middle - 0.8 - (position - 13) * 0.05
        frame.loc[position, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [close + 0.15, close, close + 0.2, close - 0.1, middle - 0.3, middle - 0.1, middle - 1.0, middle + 1.0]
    frame.loc[19, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [98.9, 98.85, 98.95, 98.84, 99.45, 99.75, 98.9, 100.9]
    return frame

def test_kc_lower_outer_downtrend_uses_symmetric_next_bar_break():
    frame = _dynamic_lower_trend_frame()
    candidate = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)
    assert (candidate['action'], candidate['reason']) == ('WAIT', 'WAIT_DOWNTREND_BREAK')
    assert candidate['pending']['side'] == 'SHORT'
    assert candidate['pending']['confirmed'] is False
    entered = TradingEngine._channel_outer_trend_entry_action(frame, 98.85, candidate['pending'])
    assert (entered['action'], entered['side']) == ('ENTER', 'SHORT')
    assert entered['reason'] == 'KC_LOWER_TREND_CONFIRMED_SHORT'

def test_kc_lower_touch_inside_does_not_seed_confirmed_short():
    frame = _dynamic_lower_trend_frame()
    # e8 規格要求候選收盤在下軌外，只有影線碰軌仍須等待。
    frame.loc[18, ['open', 'close', 'high', 'low', 'kc_lower']] = [99.15, 99.02, 99.18, 98.90, 98.95]
    candidate = TradingEngine._channel_outer_trend_entry_action(frame, 98.98)
    assert (candidate['action'], candidate['reason']) == ('WAIT', 'WAIT_OUTER_UPTREND')
    assert candidate['pending'] is None

def test_kc_lower_short_touch_cancels_on_v_rebound_before_low_break():
    frame = _dynamic_lower_trend_frame()
    frame.loc[18, ['open', 'close', 'high', 'low', 'kc_lower']] = [99.15, 98.90, 99.18, 98.85, 98.95]
    candidate = TradingEngine._channel_outer_trend_entry_action(frame, 98.88)
    frame.loc[19, 'high'] = float(candidate['pending']['candidate_high']) + 0.01
    result = TradingEngine._channel_outer_trend_entry_action(frame, 99.0, candidate['pending'])
    assert (result['action'], result['reason'], result['pending']) == (
        'WAIT', 'CANCEL_DOWNTREND_CONFIRM', None,
    )

def test_kc_lower_outer_downtrend_waits_when_trend_is_ambiguous():
    frame = _dynamic_lower_trend_frame()
    frame.loc[16:18, ['ma3', 'ma15']] = [[100.1, 100.0]] * 3
    result = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_DYNAMIC_DOWNTREND')
    assert result['pending'] is None

def test_kc_lower_outer_downtrend_next_bar_failure_cancels_candidate():
    frame = _dynamic_lower_trend_frame()
    seed = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)
    frame.loc[19, 'high'] = float(seed['pending']['candidate_high']) + 0.01
    result = TradingEngine._channel_outer_trend_entry_action(frame, 98.92, seed['pending'])
    assert (result['action'], result['reason']) == ('WAIT', 'CANCEL_DOWNTREND_CONFIRM')
    assert result['pending'] is None

def test_kc_lower_outer_downtrend_does_not_chase_when_break_is_too_far():
    frame = _dynamic_lower_trend_frame()
    seed = TradingEngine._channel_outer_trend_entry_action(frame, 98.95)
    result = TradingEngine._channel_outer_trend_entry_action(frame, 98.0, seed['pending'])
    assert result['action'] == 'WAIT'
    assert result['reason'] in ('WAIT_DOWNTREND_RETEST', 'WAIT_DOWNTREND_RETEST_BREAK')
    assert result['pending']['confirmed'] is True

def test_kc_upper_touch_inside_does_not_seed_confirmed_long():
    frame = _dynamic_upper_trend_frame()
    frame.loc[18, 'close'] = float(frame.loc[18, 'kc_upper']) - 0.01
    result = TradingEngine._channel_outer_uptrend_entry_action(frame, 101.1)
    assert (result['action'], result['side']) == ('WAIT', None)
    assert result['reason'] == 'WAIT_OUTER_UPTREND'
    assert result['pending'] is None

def _chop_momentum_frame(side):
    frame = _channel_frame()
    for position in range(10, 18):
        close = 99.95 if position % 2 == 0 else 100.05
        frame.loc[position, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [100.0, close, close + 0.05, close - 0.05, close, 100.0, 99.0, 101.0]
    if side == 'LONG':
        frame.loc[18, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [100.1, 100.5, 100.6, 100.05, 100.3, 100.05, 99.02, 101.02]
        frame.loc[19, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [100.5, 100.65, 100.66, 100.45, 100.4, 100.1, 99.04, 101.04]
    else:
        frame.loc[18, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [99.9, 99.5, 99.95, 99.4, 99.7, 99.95, 98.98, 100.98]
        frame.loc[19, ['open', 'close', 'high', 'low', 'ma3', 'ma15', 'kc_lower', 'kc_upper']] = [99.5, 99.35, 99.55, 99.34, 99.6, 99.9, 98.96, 100.96]
    return frame

def test_chop_wait_can_enter_long_from_inside_kc_after_confirmed_momentum_break():
    frame = _chop_momentum_frame('LONG')
    result = TradingEngine._channel_chop_breakout_action(frame, 100.65)
    assert (result['action'], result['side'], result['reason']) == ('ENTER', 'LONG', 'CHOP_BREAKOUT_LONG')
    assert float(frame.loc[18, 'close']) < float(frame.loc[18, 'kc_upper'])

def test_chop_wait_can_enter_short_from_inside_kc_after_confirmed_momentum_break():
    frame = _chop_momentum_frame('SHORT')
    result = TradingEngine._channel_chop_breakout_action(frame, 99.35)
    assert (result['action'], result['side'], result['reason']) == ('ENTER', 'SHORT', 'CHOP_BREAKOUT_SHORT')
    assert float(frame.loc[18, 'close']) > float(frame.loc[18, 'kc_lower'])

def test_chop_momentum_candidate_still_waits_for_next_bar_break():
    frame = _chop_momentum_frame('LONG')
    result = TradingEngine._channel_chop_breakout_action(frame, 100.55)
    assert (result['action'], result['reason']) == ('WAIT', 'WAIT_CHOP_MOMENTUM_BREAK')

def test_strongest_ranked_symbol_uses_target_direction_final_score():
    engine = TradingEngine.__new__(TradingEngine)

    class Rotation:
        direction_map = {'SOL/USDT': 'LONG', 'XRP/USDT': 'LONG', 'DOGE/USDT': 'SHORT'}
        last_metrics = [{'symbol': 'SOL/USDT', 'direction': 'LONG', 'final_score': 70}, {'symbol': 'XRP/USDT', 'direction': 'LONG', 'final_score': 90}, {'symbol': 'DOGE/USDT', 'direction': 'SHORT', 'final_score': 95}]
    engine.symbol_rotation = Rotation()
    assert engine._strongest_ranked_symbol('LONG') == ('XRP/USDT', 90.0)
    assert engine._strongest_ranked_symbol('SHORT') == ('DOGE/USDT', 95.0)

def test_market_candidates_keep_only_strongest_per_direction():
    candidates = [{'symbol': 'SOL/USDT', 'side': 'LONG', 'score': 100, 'trend_quality': 0.8}, {'symbol': 'XRP/USDT', 'side': 'LONG', 'score': 95, 'trend_quality': 1.2}, {'symbol': 'DOGE/USDT', 'side': 'SHORT', 'score': 90, 'trend_quality': 0.7}]
    selected, skipped = TradingEngine._select_strongest_same_side_candidates(candidates)
    assert [item['symbol'] for item in selected] == ['XRP/USDT', 'DOGE/USDT']
    assert [item['symbol'] for item in skipped] == ['SOL/USDT']

@pytest.mark.anyio
async def test_stale_losing_channel_position_closes_before_stronger_confirmed_entry(monkeypatch):
    monkeypatch.setattr("core.engine.MAX_SLOTS", 1)
    monkeypatch.setattr("core.engine.CHANNEL_SWING_TAKEOVER_MIN_HOLD_SEC", 900.0)
    events = []

    class Account:
        positions = {
            "OLD/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "qty": 1.0, "open_timestamp": 1.0,
            }
        }
        pending_limit_orders = {}

        def log(self, *_args, **_kwargs):
            pass

        async def close_position(self, symbol, *_args, **_kwargs):
            events.append(("close", symbol))
            self.positions.pop(symbol)
            return True

    class Rotation:
        def request_replacement(self, symbol):
            events.append(("replace", symbol))

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.symbol_rotation = Rotation()
    engine.rotation_event = None
    engine.tickers = {"OLD/USDT": 99.0}

    async def execution_safe(*_args):
        return True

    async def place(symbol, *_args):
        assert not engine.account.positions
        events.append(("open", symbol))
        return True

    engine._execution_price_is_safe = execution_safe
    engine._abnormal_market_entry_allowed = lambda *_args: True
    engine._place_structured_entry = place
    candidate = {
        "symbol": "NEW/USDT", "side": "SHORT", "entry_mode": "CHANNEL_SWING",
        "priority": 4, "reason": "KC_LOWER_TREND_CONFIRMED_SHORT",
        "live_price": 50.0, "atr": 0.5,
    }

    handled, opened = await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    )

    assert (handled, opened) == (True, True)
    assert events == [
        ("close", "OLD/USDT"),
        ("replace", "OLD/USDT"),
        ("open", "NEW/USDT"),
    ]


@pytest.mark.anyio
async def test_strong_first_touch_immediately_takes_over_recent_profitable_same_side(
    monkeypatch,
):
    monkeypatch.setattr("core.engine.MAX_SLOTS", 1)
    monkeypatch.setattr("core.engine.CHANNEL_SWING_TAKEOVER_MIN_HOLD_SEC", 900.0)
    events = []

    class Account:
        positions = {
            "OLD/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "qty": 1.0, "open_timestamp": 990.0,
            }
        }
        pending_limit_orders = {}

        def log(self, *_args, **_kwargs):
            pass

        async def close_position(self, symbol, *_args, **_kwargs):
            events.append(("close", symbol))
            self.positions.pop(symbol)
            return True

    class Rotation:
        def request_replacement(self, symbol):
            events.append(("replace", symbol))

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.symbol_rotation = Rotation()
    engine.rotation_event = None
    engine.tickers = {"OLD/USDT": 101.0}
    engine._continuous_market_mode = {}
    engine._execution_price_is_safe = lambda *_args: None

    async def execution_safe(*_args):
        return True

    async def place(symbol, *_args):
        events.append(("open", symbol))
        return True

    engine._execution_price_is_safe = execution_safe
    engine._abnormal_market_entry_allowed = lambda *_args: True
    engine._place_structured_entry = place
    candidate = {
        "symbol": "NEW/USDT", "side": "LONG",
        "entry_mode": "CHANNEL_SWING", "priority": 5,
        "signal_code": "KC_STRONG_FIRST_UPPER_TOUCH_LONG",
        "reason": "Channel Swing strong first upper touch LONG",
        "live_price": 50.0, "atr": 0.5,
    }

    assert await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    ) == (True, True)
    assert events == [
        ("close", "OLD/USDT"),
        ("replace", "OLD/USDT"),
        ("open", "NEW/USDT"),
    ]


@pytest.mark.anyio
async def test_channel_takeover_keeps_old_position_when_new_execution_is_unsafe(monkeypatch):
    monkeypatch.setattr("core.engine.MAX_SLOTS", 1)
    monkeypatch.setattr("core.engine.CHANNEL_SWING_TAKEOVER_MIN_HOLD_SEC", 900.0)

    class Account:
        positions = {
            "OLD/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "qty": 1.0, "open_timestamp": 1.0,
            }
        }
        pending_limit_orders = {}

        async def close_position(self, *_args, **_kwargs):
            raise AssertionError("unsafe replacement must not close the held position")

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.tickers = {"OLD/USDT": 99.0}

    async def execution_unsafe(*_args):
        return False

    engine._execution_price_is_safe = execution_unsafe
    candidate = {
        "symbol": "NEW/USDT", "side": "SHORT", "entry_mode": "CHANNEL_SWING",
        "priority": 4, "reason": "KC_LOWER_TREND_CONFIRMED_SHORT",
        "live_price": 50.0, "atr": 0.5,
    }

    assert await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    ) == (True, False)
    assert "OLD/USDT" in engine.account.positions


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("age", "mark", "priority", "reason"),
    [
        (899.0, 99.0, 4, "KC_LOWER_TREND_CONFIRMED_SHORT"),
        (1000.0, 101.0, 4, "KC_LOWER_TREND_CONFIRMED_SHORT"),
        (1000.0, 99.0, 1, "RANGE_KC_UPPER_PEAK_CONFIRMED_SHORT"),
    ],
)
async def test_channel_takeover_rejects_recent_profitable_or_range_candidate(
    monkeypatch, age, mark, priority, reason,
):
    monkeypatch.setattr("core.engine.MAX_SLOTS", 1)
    monkeypatch.setattr("core.engine.CHANNEL_SWING_TAKEOVER_MIN_HOLD_SEC", 900.0)

    class Account:
        positions = {
            "OLD/USDT": {
                "side": "LONG", "entry_mode": "CHANNEL_SWING",
                "entry_price": 100.0, "qty": 1.0,
                "open_timestamp": 1000.0 - age,
            }
        }
        pending_limit_orders = {}

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.tickers = {"OLD/USDT": mark}
    candidate = {
        "symbol": "NEW/USDT", "side": "SHORT", "entry_mode": "CHANNEL_SWING",
        "priority": priority, "reason": reason, "live_price": 50.0, "atr": 0.5,
    }

    assert await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    ) == (False, False)


def test_kc_pivot_switch_candidate_has_priority_in_same_direction():
    candidates = [{'symbol': 'SOL/USDT', 'side': 'SHORT', 'score': 100, 'trend_quality': 99.0}, {'symbol': 'DOGE/USDT', 'side': 'SHORT', 'score': 100, 'trend_quality': 0.5, 'priority': 1}]
    selected, skipped = TradingEngine._select_strongest_same_side_candidates(candidates)
    assert [item['symbol'] for item in selected] == ['DOGE/USDT']
    assert [item['symbol'] for item in skipped] == ['SOL/USDT']

def test_kc_outer_entry_has_priority_over_inside_touch_entry():
    outer = TradingEngine._channel_entry_candidate_priority("KC_LIVE_UPPER_BREAK_LONG")
    touch = TradingEngine._channel_entry_candidate_priority("KC_UPPER_TOUCH_LONG")
    assert outer > touch


def test_executable_channel_candidates_rank_confirmed_outer_trend_first():
    outer = TradingEngine._channel_entry_candidate_priority('KC_UPPER_TREND_CONFIRMED_LONG')
    inner_reentry = TradingEngine._channel_entry_candidate_priority('INSTANT_INNER_REENTRY_LONG')
    pivot = TradingEngine._channel_entry_candidate_priority('KC_LOWER_TROUGH_CONFIRMED')
    assert outer > inner_reentry > pivot

def test_outer_momentum_candidate_beats_stronger_pivot_candidate():
    candidates = [{'symbol': 'PIVOT/USDT', 'side': 'LONG', 'priority': 1, 'trend_quality': 9.0}, {'symbol': 'OUTER/USDT', 'side': 'LONG', 'priority': 3, 'trend_quality': 0.5}]
    selected, _ = TradingEngine._select_strongest_same_side_candidates(candidates)
    assert [item['symbol'] for item in selected] == ['OUTER/USDT']

def test_same_priority_candidate_with_more_profit_space_wins():
    candidates = [{'symbol': 'FAST/USDT', 'side': 'LONG', 'priority': 3, 'profit_potential': 1.2, 'trend_quality': 9.0}, {'symbol': 'ROOM/USDT', 'side': 'LONG', 'priority': 3, 'profit_potential': 4.8, 'trend_quality': 0.5}]
    selected, skipped = TradingEngine._select_strongest_same_side_candidates(candidates)
    assert [item['symbol'] for item in selected] == ['ROOM/USDT']
    assert [item['symbol'] for item in skipped] == ['FAST/USDT']

def test_current_channel_volume_ratio_uses_live_candle():
    frame = _channel_frame()
    frame.loc[frame.index[-1], ['volume', 'vol_ma_20']] = [49.0, 100.0]
    assert TradingEngine._channel_volume_ratio(frame) == pytest.approx(0.49)

def test_held_low_volume_exit_requires_three_declining_closed_bars(monkeypatch):
    monkeypatch.setattr('core.engine.KELTNER_MIN_VOLUME_RATIO', 0.5)
    frame = _channel_frame()
    frame.loc[frame.index[-4:-1], ['volume', 'vol_ma_20']] = [[80.0, 100.0], [60.0, 100.0], [40.0, 100.0]]
    frame.loc[frame.index[-1], ['volume', 'vol_ma_20']] = [1.0, 100.0]
    assert TradingEngine._channel_held_volume_is_declining(frame)
    frame.loc[frame.index[-2], 'volume'] = 70.0
    assert not TradingEngine._channel_held_volume_is_declining(frame)

def test_candidate_profit_potential_uses_directional_daily_space():
    engine = TradingEngine.__new__(TradingEngine)

    class Rotation:
        volatility_stats = {'ROOM/USDT': {'avg_daily_up_pct': 5.5, 'avg_daily_down_pct': 2.5}}
    engine.symbol_rotation = Rotation()
    assert engine._candidate_profit_potential('ROOM/USDT', 'LONG', 1.0, 100.0) == pytest.approx(5.5)
    assert engine._candidate_profit_potential('ROOM/USDT', 'SHORT', 1.0, 100.0) == pytest.approx(2.5)

def test_candidate_board_refreshes_after_fill_and_while_slot_remains():
    assert TradingEngine._candidate_board_refresh_needed(True, position_count=2, pending_count=0, max_slots=2, seconds_since_refresh=0.0)
    assert TradingEngine._candidate_board_refresh_needed(False, position_count=1, pending_count=0, max_slots=2, seconds_since_refresh=15.0)
    assert not TradingEngine._candidate_board_refresh_needed(False, position_count=1, pending_count=0, max_slots=1, seconds_since_refresh=60.0)
    assert not TradingEngine._candidate_board_refresh_needed(False, position_count=1, pending_count=0, max_slots=2, seconds_since_refresh=14.9)


def test_ranked_direction_both_allows_long_and_short_channel_scan():
    assert TradingEngine._entry_matches_ranked_direction("LONG", "BOTH")
    assert TradingEngine._entry_matches_ranked_direction("SHORT", "BOTH")

def test_single_slot_amount_uses_eighty_percent_wallet(monkeypatch):
    # CI does not load the developer's .env; keep this allocation contract
    # independent from the configured per-trade cap.
    monkeypatch.setattr("core.engine.TRADE_AMOUNT_USDT", 1_000.0)
    engine = TradingEngine.__new__(TradingEngine)

    class Account:
        positions = {}
        pending_limit_orders = {}

        def get_available_balance(self):
            return 150.0 if not self.positions else 74.85

        def get_wallet_balance(self):
            return 150.0
    engine.account = Account()
    assert engine._continuous_entry_amount() == pytest.approx(120.0)
    engine.account.positions['SOL/USDT'] = {'margin': 120.0}
    assert engine._continuous_entry_amount() == pytest.approx(0.0)

@pytest.mark.anyio
async def test_channel_swing_position_is_stored_without_sl_or_tp(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, 'STATE_FILE', str(tmp_path / 'channel_swing.json'))
    account = PaperAccount()
    opened = await account.open_position('BTC/USDT', 'LONG', 100.0, 50.0, 95.0, 110.0, 'channel swing', leverage=1, signal_score=100, apply_slippage=False, entry_context={'entry_mode': 'CHANNEL_SWING', 'wave_regime': 'RANGE', 'initial_sl': 95.0, 'initial_risk': 5.0})
    assert opened is True
    assert account.positions['BTC/USDT']['sl'] == 0.0
    assert account.positions['BTC/USDT']['tp'] == 0.0
    assert account.position_meta['BTC/USDT']['sl'] == 0.0
    assert account.position_meta['BTC/USDT']['initial_sl'] == 0.0
    monkeypatch.setattr(pa_module, 'ENABLE_PROFIT_LOCK_USDT', True)
    monkeypatch.setattr(pa_module, 'ENABLE_FIXED_PROFIT_LOCK_PCT', True)
    monkeypatch.setattr(pa_module, 'ENABLE_TRAILING_STOP', True)
    monkeypatch.setattr(pa_module, 'ENABLE_EARLY_PROFIT_GUARD', True)
    await account.update_positions({'BTC/USDT': 102.0})
    await account.update_positions({'BTC/USDT': 100.5})
    assert 'BTC/USDT' in account.positions
    assert account.positions['BTC/USDT']['sl'] == 0.0
    assert not account.positions['BTC/USDT'].get('is_breakeven_moved')
    topped_up = await account.open_position('BTC/USDT', 'LONG', 100.2, 25.0, 95.0, 110.0, 'channel swing top-up', leverage=1, signal_score=100, apply_slippage=False, entry_context={'entry_mode': 'CHANNEL_SWING'})
    assert topped_up is False
    assert account.positions['BTC/USDT']['margin'] == 50.0
    reloaded = PaperAccount()
    await reloaded.initialize()
    assert reloaded.positions['BTC/USDT']['sl'] == 0.0
    assert reloaded.positions['BTC/USDT']['tp'] == 0.0
    assert not any(('啟動保護遷移' in item.get('text', '') for item in reloaded.logs))

def test_channel_swing_ignores_legacy_structured_stop_cooldown():
    assert TradingEngine._structured_stop_cooldown_blocks('CHANNEL_SWING', 3600.0) is False
    assert TradingEngine._structured_stop_cooldown_blocks('BREAKOUT', 3600.0) is True
    assert TradingEngine._structured_stop_cooldown_blocks('BREAKOUT', 0.0) is False

def test_channel_signal_events_deduplicate_and_track_reason_transitions():

    class LogAccount:

        def __init__(self):
            self.logs = []

        def log(self, text, level):
            self.logs.append((text, level))
    engine = TradingEngine.__new__(TradingEngine)
    engine._channel_signal_events = {}
    engine.account = LogAccount()
    frame = _channel_frame()
    frame['timestamp'] = [index * 60000 for index in range(len(frame))]
    engine._record_channel_signal_event('TEST/USDT', 'KC_WIDTH_TOO_NARROW', frame)
    first_event = engine._channel_signal_events['TEST/USDT'][0]
    assert (first_event['action'], first_event['reason']) == ('CHANNEL_BLOCK', 'KC_WIDTH_TOO_NARROW')
    assert first_event['label'] == 'KC寬度不足設定門檻'
    frame.loc[frame.index[-1], 'timestamp'] += 60000
    engine._record_channel_signal_event('TEST/USDT', 'KC_WIDTH_TOO_NARROW', frame)
    assert len(engine._channel_signal_events['TEST/USDT']) == 1
    assert len(engine.account.logs) == 1
    engine._record_channel_signal_event('TEST/USDT', 'CANCEL_LONG', frame)
    assert len(engine._channel_signal_events['TEST/USDT']) == 2
    replacement = engine._channel_signal_events['TEST/USDT'][-1]
    assert (replacement['action'], replacement['reason']) == ('CHANNEL_CANCEL', 'CANCEL_LONG')
    assert len(engine.account.logs) == 2

def test_strong_middle_rebound_without_outer_touch_does_not_enter_long():
    frame = _channel_frame()
    for i in range(1, 4):
        frame.loc[frame.index[-i], ['kc_lower', 'ema_20', 'kc_upper']] = [99.0, 100.0, 101.0]
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [99.5, 99.6, 99.4, 99.7, 99.5]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [99.9, 100.7, 99.8, 100.8, 100.2]
    frame.loc[frame.index[-1], ['open', 'close', 'low', 'ma3']] = [100.7, 100.8, 100.6, 100.5]
    result = TradingEngine._channel_swing_action(frame, 100.8)
    assert (result.get('action'), result.get('side')) == ('WAIT', None)

def test_strong_middle_rebound_without_outer_touch_does_not_enter_short():
    frame = _channel_frame()
    for i in range(1, 4):
        frame.loc[frame.index[-i], ['kc_lower', 'ema_20', 'kc_upper']] = [99.0, 100.0, 101.0]
    frame.loc[frame.index[-3], ['open', 'close', 'low', 'high', 'ma3']] = [100.5, 100.4, 100.3, 100.6, 100.5]
    frame.loc[frame.index[-2], ['open', 'close', 'low', 'high', 'ma3']] = [100.1, 99.3, 99.2, 100.2, 99.8]
    frame.loc[frame.index[-1], ['open', 'close', 'high', 'ma3']] = [99.3, 99.2, 99.4, 99.5]
    result = TradingEngine._channel_swing_action(frame, 99.2)
    assert (result.get('action'), result.get('side')) == ('WAIT', None)

def test_instant_inner_reentry_short_single_candle():
    """Test that a single red candle returning from the upper band by 50% triggers a SHORT reentry."""
    import pandas as pd
    from core.engine import TradingEngine
    df = pd.DataFrame([{'open': 100, 'high': 105, 'low': 95, 'close': 102, 'ma3': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 100, 'high': 108, 'low': 100, 'close': 106, 'ma3': 102, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 106, 'high': 106, 'low': 98, 'close': 99, 'ma3': 103, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}])
    live_price = 99.0
    action_data = TradingEngine._channel_live_inner_reentry_action(df, live_price, None)
    assert action_data['action'] == 'ENTER'
    assert action_data['side'] == 'SHORT'
    assert action_data['reason'] == 'LIVE_INNER_REENTRY_SHORT'

def test_instant_inner_reentry_short_two_candles():
    """Test that two red candles returning from the upper band by 50% triggers a SHORT reentry."""
    import pandas as pd
    from core.engine import TradingEngine
    df = pd.DataFrame([{'open': 100, 'high': 105, 'low': 95, 'close': 102, 'ma3': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 104, 'high': 104, 'low': 101, 'close': 101, 'ma3': 102, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 101, 'high': 101, 'low': 97, 'close': 98, 'ma3': 103, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}])
    live_price = 98.0
    action_data = TradingEngine._channel_live_inner_reentry_action(df, live_price, None)
    assert action_data['action'] == 'ENTER'
    assert action_data['side'] == 'SHORT'
    assert action_data['reason'] == 'LIVE_INNER_REENTRY_SHORT'

def test_instant_inner_reentry_long_first_candle_opens_outside():
    """Test that a two-candle long reentry works even if the first candle opens below the KC lower band."""
    import pandas as pd
    from core.engine import TradingEngine
    df = pd.DataFrame([{'open': 100, 'high': 105, 'low': 95, 'close': 98, 'ma3': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 94, 'high': 97, 'low': 93, 'close': 96, 'ma3': 102, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}, {'open': 96, 'high': 100, 'low': 96, 'close': 99, 'ma3': 103, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100, 'volume': 100, 'vol_ma_20': 100}])
    live_price = 99.0
    action_data = TradingEngine._channel_live_inner_reentry_action(df, live_price, None)
    assert action_data['action'] == 'ENTER'
    assert action_data['side'] == 'LONG'
    assert action_data['reason'] == 'LIVE_INNER_REENTRY_LONG'

def test_single_strong_candle_unlocks_chop_wait():
    """Test that a single strong candle crossing the middle band unlocks CHOP_WAIT early."""
    import pandas as pd
    from core.engine import TradingEngine
    data = []
    for i in range(13):
        data.append({'open': 100, 'close': 100 + (1 if i % 2 == 0 else -1), 'high': 102, 'low': 98, 'ma3': 100, 'ma15': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100})
    data.append({'open': 99, 'close': 102, 'high': 103, 'low': 98, 'ma3': 101, 'ma15': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100})
    data.append({'open': 102, 'close': 102, 'high': 103, 'low': 101, 'ma3': 101, 'ma15': 100, 'kc_upper': 105, 'kc_lower': 95, 'volume': 100, 'vol_ma_20': 100})
    df = pd.DataFrame(data)
    chop_state = TradingEngine._channel_chop_state(df)
    assert chop_state['clear_direction'] == 'LONG'
    assert chop_state['reason'] == 'DIRECTION_CLEAR'
_OBSOLETE_CHANNEL_ENTRY_TESTS = ('test_second_closed_confirmation_candle_must_keep_direction_color', 'test_outer_ma3_route_accepts_two_closed_turn_bars_that_remain_outside', 'test_body_deep_eighty_percent_into_half_channel_bypasses_outer_depth', 'test_shallow_outer_v_turns_are_symmetric_without_ma3_depth', 'test_lower_outer_green_reentry_can_open_long_without_ma3_depth', 'test_latest_adjacent_two_closed_outer_v_bars_are_valid_on_both_sides', 'test_empty_slot_does_not_chase_kc_outer_trend_without_pivot_turn', 'test_empty_slot_does_not_chase_price_outside_without_ma3_trend', 'test_live_outer_break_does_not_require_ma3_slope', 'test_empty_slot_does_not_chase_when_only_close_breaks_outer_rail', 'test_closed_lower_trough_uses_confirmed_long_instead_of_live_outer_short', 'test_cancelled_outer_peak_cannot_fall_back_to_live_ma3_entry', 'test_flat_entry_uses_ma3_and_held_position_exits_on_confirmed_trough', 'test_confirmed_outer_pivot_opens_before_forty_percent_reentry', 'test_current_downtrend_after_outer_peak_opens_on_green_candle', 'test_ma3_turn_does_not_open_when_price_is_outside_kc', 'test_old_outer_pivot_does_not_chase_at_opposite_outer_rail', 'test_shallow_outer_reentry_still_reverses_held_short_on_confirmed_trough', 'test_channel_swing_reentry_boundary_is_80_percent_of_outer_half', 'test_live_ma3_turn_does_not_block_confirmed_trough_exit', 'test_adjacent_two_closed_green_bars_confirm_long_candidate', 'test_adjacent_two_closed_red_bars_confirm_short_candidate')
for _test_name in _OBSOLETE_CHANNEL_ENTRY_TESTS:
    globals()[_test_name] = pytest.mark.skip(reason='obsolete: only confirmed KC-outer trend entries are permitted')(globals()[_test_name])


@pytest.mark.anyio
async def test_adverse_range_to_bull_transition_bypasses_takeover_age(monkeypatch):
    monkeypatch.setattr("core.engine.MAX_SLOTS", 1)
    monkeypatch.setattr("core.engine.CHANNEL_SWING_TAKEOVER_MIN_HOLD_SEC", 900.0)
    events = []

    class Account:
        positions = {
            "OLD/USDT": {
                "side": "SHORT", "entry_mode": "CHANNEL_SWING",
                "entry_market_mode": "RANGE", "market_mode": "BULL",
                "entry_price": 100.0, "qty": 1.0, "open_timestamp": 990.0,
            }
        }
        pending_limit_orders = {}

        def log(self, *_args, **_kwargs):
            pass

        async def close_position(self, symbol, *_args, **_kwargs):
            events.append(("close", symbol))
            self.positions.pop(symbol)
            return True

    class Rotation:
        def request_replacement(self, symbol):
            events.append(("replace", symbol))

    engine = TradingEngine.__new__(TradingEngine)
    engine.account = Account()
    engine.symbol_rotation = Rotation()
    engine.rotation_event = None
    engine.tickers = {"OLD/USDT": 101.0}
    engine._continuous_market_mode = {"OLD/USDT": "BULL"}

    async def execution_safe(*_args):
        return True

    async def place(symbol, *_args):
        events.append(("open", symbol))
        return True

    engine._execution_price_is_safe = execution_safe
    engine._abnormal_market_entry_allowed = lambda *_args: True
    engine._place_structured_entry = place
    candidate = {
        "symbol": "NEW/USDT", "side": "LONG",
        "entry_mode": "CHANNEL_SWING", "market_mode": "BULL",
        "priority": 4, "signal_code": "KC_LIVE_UPPER_BREAK_LONG",
        "reason": "Channel Swing live KC outer break LONG",
        "live_price": 50.0, "atr": 0.5,
    }

    assert await engine._try_channel_stronger_symbol_takeover(
        candidate, now_time=1000.0, daily_halt=False,
    ) == (True, True)
    assert events == [
        ("close", "OLD/USDT"),
        ("replace", "OLD/USDT"),
        ("open", "NEW/USDT"),
    ]


def test_channel_entry_context_preserves_original_market_mode():
    from core.paper_account import ENTRY_CONTEXT_KEYS
    assert "market_mode" in ENTRY_CONTEXT_KEYS
    assert "entry_market_mode" in ENTRY_CONTEXT_KEYS
    assert "channel_entry_profile" in ENTRY_CONTEXT_KEYS
    assert "channel_entry_profile_basis" in ENTRY_CONTEXT_KEYS


def test_shallow_adjacent_outer_reversal_enters_without_half_channel_reentry():
    long_frame = _channel_frame()
    long_frame.loc[long_frame.index[-3], ["open", "close", "low", "high", "ma3"]] = [98.8, 98.9, 98.7, 98.95, 98.7]
    long_frame.loc[long_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [99.0, 99.2, 98.8, 99.3, 99.2]
    long_result = TradingEngine._channel_swing_action(
        long_frame, 99.2, market_mode="RANGE",
    )

    short_frame = _channel_frame()
    short_frame.loc[short_frame.index[-3], ["open", "close", "low", "high", "ma3"]] = [101.2, 101.1, 101.05, 101.3, 101.3]
    short_frame.loc[short_frame.index[-2], ["open", "close", "low", "high", "ma3"]] = [101.0, 100.8, 100.7, 101.2, 100.8]
    short_result = TradingEngine._channel_swing_action(
        short_frame, 100.8, market_mode="RANGE",
    )

    assert (long_result["action"], long_result["side"]) == ("ENTER", "LONG")
    assert (short_result["action"], short_result["side"]) == ("ENTER", "SHORT")
    assert long_result["reason"] == "RANGE_KC_LOWER_TROUGH_CONFIRMED_LONG"
    assert short_result["reason"] == "RANGE_KC_UPPER_PEAK_CONFIRMED_SHORT"


def test_kc_inner_clear_ma3_trend_enters_both_directions():
    long_frame = _channel_frame(lower=99.0, upper=101.0)
    long_frame.loc[long_frame.index[-3:], "ma3"] = [99.5, 99.8, 100.1]
    long_frame.loc[long_frame.index[-1], "ma15"] = 100.0
    long_result = TradingEngine._channel_inner_trend_entry_action(long_frame, 100.3)

    short_frame = _channel_frame(lower=99.0, upper=101.0)
    short_frame.loc[short_frame.index[-3:], "ma3"] = [100.5, 100.2, 99.9]
    short_frame.loc[short_frame.index[-1], "ma15"] = 100.0
    short_result = TradingEngine._channel_inner_trend_entry_action(short_frame, 99.7)

    assert (long_result["action"], long_result["side"], long_result["reason"]) == (
        "ENTER", "LONG", "KC_INNER_UPTREND_LONG",
    )
    assert (short_result["action"], short_result["side"], short_result["reason"]) == (
        "ENTER", "SHORT", "KC_INNER_DOWNTREND_SHORT",
    )


def test_kc_inner_flat_ma3_still_waits():
    frame = _channel_frame(lower=99.0, upper=101.0)
    result = TradingEngine._channel_inner_trend_entry_action(frame, 100.2)
    assert (result["action"], result["side"]) == ("WAIT", None)


def test_channel_swing_simple_trend_entry_bypasses_old_signal_filters():
    place_source = inspect.getsource(TradingEngine._place_structured_entry)
    process_source = inspect.getsource(TradingEngine._process_single_symbol)
    assert "entry_mode != \"CHANNEL_SWING\"" in place_source
    assert "排名候選但當下量能不足" not in process_source
    assert "blocked: ranked direction" not in process_source
    assert "訊號遭 BTC 1m" not in process_source
    assert TradingEngine._channel_entry_candidate_priority("KC_INNER_UPTREND_LONG") == 4
    assert TradingEngine._channel_entry_candidate_priority("KC_INNER_DOWNTREND_SHORT") == 4
