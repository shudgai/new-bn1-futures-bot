import pandas as pd

from core.services.dual_track_breakout_service import DualTrackBreakoutStateMachine


def bar(**overrides):
    values = {
        "open": 100.0,
        "close": 101.5,
        "high": 102.0,
        "low": 99.5,
        "atr": 1.0,
        "kc_upper": 101.0,
        "kc_lower": 99.0,
    }
    values.update(overrides)
    return pd.Series(values)


def history_for(current):
    rows = [bar(high=100.4, low=99.6), bar(high=100.6, low=99.4)]
    rows.append(current)
    return pd.DataFrame(rows)


def test_extreme_long_entry_requires_profit_space_and_enters_same_bar():
    machine = DualTrackBreakoutStateMachine()
    current = bar(close=103.0, high=104.0, low=100.0)

    decision = machine.process_closed_bar(current, history_for(current))

    assert decision.action == "ENTER"
    assert decision.side == "LONG"
    assert machine.position == "LONG"
    assert machine.pending_signal is None


def test_normal_breakout_waits_for_same_color_confirmation():
    machine = DualTrackBreakoutStateMachine()
    first = bar(close=101.2, high=101.4, low=100.0)
    second = bar(open=101.2, close=101.8, high=102.0, low=101.0)

    assert machine.process_closed_bar(first).reason == "STANDARD_BREAKOUT_PENDING"
    decision = machine.process_closed_bar(second)

    assert decision.action == "ENTER"
    assert decision.reason == "STANDARD_CONFIRMATION"


def test_doji_extends_pending_and_opposite_candle_cancels():
    machine = DualTrackBreakoutStateMachine()
    first = bar(close=101.2, high=101.4, low=100.0)
    doji = bar(open=101.2, close=101.2, high=101.4, low=101.0)
    opposite = bar(open=101.2, close=100.8, high=101.3, low=100.5)

    machine.process_closed_bar(first)
    assert machine.process_closed_bar(doji).reason == "DOJI_EXTENDS_PENDING"
    assert machine.process_closed_bar(opposite).reason == "OPPOSITE_CANDLE_CANCELLED"
    assert machine.pending_signal is None


def test_close_bar_never_reverses_on_the_same_bar_even_when_extreme():
    machine = DualTrackBreakoutStateMachine()
    machine.position = "LONG"
    reversal = bar(open=104.0, close=96.0, high=104.5, low=95.0, kc_lower=99.0)

    decision = machine.process_closed_bar(reversal)

    assert decision.action == "EXIT"
    assert decision.side == "LONG"
    assert machine.position is None
    assert machine.pending_signal == "SHORT"


def test_special_profit_check_rejects_body_with_insufficient_ratio():
    machine = DualTrackBreakoutStateMachine()
    current = bar(close=103.0, high=106.0, low=99.0)

    assert machine.has_sufficient_profit_space("LONG", current, history_for(current)) is False


def test_special_short_entry_uses_the_mirrored_profit_space_rules():
    machine = DualTrackBreakoutStateMachine()
    current = bar(open=97.0, close=93.0, high=97.5, low=92.0, kc_upper=101.0, kc_lower=99.0)

    decision = machine.process_closed_bar(current, history_for(current))

    assert decision.action == "ENTER"
    assert decision.side == "SHORT"


def test_pending_signal_expires_after_three_following_closed_bars():
    machine = DualTrackBreakoutStateMachine()
    first = bar(close=101.2, high=101.4, low=100.0)
    doji = bar(open=101.2, close=101.2, high=101.4, low=101.0)

    machine.process_closed_bar(first)
    machine.process_closed_bar(doji)
    machine.process_closed_bar(doji)
    machine.process_closed_bar(doji)
    decision = machine.process_closed_bar(doji)

    assert decision.reason == "PENDING_SIGNAL_TIMEOUT"
    assert machine.pending_signal is None
    assert machine.pending_bars_count == 0
    assert machine.is_special_k is False


def test_position_sync_overrides_local_ghost_position():
    machine = DualTrackBreakoutStateMachine()
    machine.position = "LONG"
    machine.pending_signal = "SHORT"

    decision = machine.process_closed_bar(bar(close=100.2), actual_position_amt=0.0)

    assert decision.reason == "NO_BREAKOUT"
    assert machine.position is None
    assert machine.pending_signal is None


def test_frame_preparation_keeps_latest_hundred_rows_and_resets_index():
    frame = pd.DataFrame({"close": range(150)}, index=range(1000, 1150))

    prepared = DualTrackBreakoutStateMachine.prepare_frame(frame)

    assert len(prepared) == 100
    assert prepared.index.tolist() == list(range(100))
    assert prepared.iloc[0]["close"] == 50


def test_filled_order_result_clears_trigger_state():
    machine = DualTrackBreakoutStateMachine()
    machine.pending_signal = "LONG"
    machine.pending_bars_count = 2
    machine.is_special_k = True
    decision = machine._open("LONG", "EXTREME_CANDLE_DIRECT_ENTRY")

    machine.on_order_result(decision, filled=True)

    assert machine.position == "LONG"
    assert machine.pending_signal is None
    assert machine.pending_bars_count == 0
    assert machine.is_special_k is False
