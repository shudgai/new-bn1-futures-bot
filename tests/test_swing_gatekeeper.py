from dataclasses import replace

import pytest

from core.swing import SwingState
from core.swing.gatekeeper import YaoBiEntryGatekeeper as G
from tests.test_swing_core import _with_last, breakout_bars, trend_bars


def ok(bars, side="LONG", state=None, breakout=False):
    return G.can_enter(bars, side, state, breakout)


def test_clean_trend_passes():
    assert ok(trend_bars("LONG")) == (True, "PASSED_GATEKEEPER")
    assert ok(trend_bars("SHORT"), "SHORT") == (True, "PASSED_GATEKEEPER")


def test_fail_closed_on_short_history_unclosed_or_bad_atr():
    assert ok(trend_bars()[-10:])[1] == "FORBIDDEN_INSUFFICIENT_HISTORY"
    assert ok(_with_last(trend_bars(), is_closed=False))[1] == "FORBIDDEN_INSUFFICIENT_HISTORY"
    assert ok(_with_last(trend_bars(), atr=0.0))[1] == "FORBIDDEN_BAD_ATR"


@pytest.mark.parametrize("ma5_prev_delta,side,reason", [
    (0.1, "LONG", "FORBIDDEN_MA5_SLOPING_DOWN"),     # ma5 falls
    (0.0, "LONG", "FORBIDDEN_MA5_SLOPING_DOWN"),     # ma5 flat
    (-0.1, "SHORT", "FORBIDDEN_MA5_SLOPING_UP"),
    (0.0, "SHORT", "FORBIDDEN_MA5_SLOPING_UP"),
])
def test_ma5_direction_conflict(ma5_prev_delta, side, reason):
    bars = trend_bars(side)
    last = bars[-1]
    prev_ma5 = last.ma5 + (ma5_prev_delta if side == "LONG" else ma5_prev_delta)
    if side == "LONG":
        prev_ma5 = last.ma5 + (ma5_prev_delta or 0.0)
    bars[-2] = replace(bars[-2], ma5=prev_ma5 if ma5_prev_delta else last.ma5)
    assert ok(bars, side)[1] == reason


def test_five_lines_flat_and_ma5_flat():
    flat = [replace(b, ma5=100.0, ma15=100.0, kc_upper=104.0, kc_lower=96.0, close=100.4) for b in trend_bars()]
    flat[-1] = replace(flat[-1], ma5=100.04)  # tiny up-slope, below 0.05 ATR
    assert ok(flat)[1] == "FORBIDDEN_LINES_FLAT"
    # MA5 flat alone (other lines trending)
    bars = trend_bars()
    bars[-2] = replace(bars[-2], ma5=bars[-1].ma5 - 0.01)
    assert ok(bars)[1] == "FORBIDDEN_LINES_FLAT"


def test_sine_wave_ma5_blocked():
    bars = trend_bars()
    for i in range(-12, -1):
        bars[i] = replace(bars[i], ma5=bars[i].ma15 - 0.3 + (0.15 if i % 2 else 0.0))
    bars[-1] = replace(bars[-1], ma5=bars[-1].ma15 + 0.1)
    assert ok(bars)[1] == "FORBIDDEN_CHOP_MARKET"


def test_midline_whipsaw_12_window_two_crosses():
    bars = trend_bars()
    # crosses at index -12 (outside old 10-bar window) still counted
    bars[-12] = replace(bars[-12], close=bars[-12].ma15 - 0.4)
    bars[-11] = replace(bars[-11], close=bars[-11].ma15 + 0.4)
    bars[-10] = replace(bars[-10], close=bars[-10].ma15 - 0.4)
    bars[-9] = replace(bars[-9], close=bars[-9].ma15 + 0.4)
    assert ok(bars)[1] == "FORBIDDEN_CHOP_MARKET"


def test_repeated_ma_crosses_blocked():
    bars = trend_bars()
    bars[-6] = replace(bars[-6], ma5=bars[-6].ma15 + 0.1)  # an earlier cross up then back down
    assert ok(bars)[1] == "FORBIDDEN_CHOP_MARKET"


def test_chase_ceiling_and_floor():
    assert ok(_with_last(trend_bars(), kc_upper=101.5, kc_lower=95.0))[1] == "FORBIDDEN_CHASE_CEILING"
    short = trend_bars("SHORT")
    bars = _with_last(short, kc_upper=short[-1].close + 5.0, kc_lower=short[-1].close - 1.0)
    assert ok(bars, "SHORT")[1] == "FORBIDDEN_CHASE_FLOOR"
    bars = _with_last(trend_bars(), kc_upper=trend_bars()[-1].close)  # close >= upper
    assert ok(bars)[1] == "FORBIDDEN_CHASE_CEILING"


def test_overextended_from_ma15():
    bars = trend_bars()
    last = bars[-1]
    bars[-1] = replace(last, close=last.ma15 + 1.6, open=last.ma15 + 1.3, high=last.ma15 + 1.7,
                       low=last.ma15 + 1.2, kc_upper=last.ma15 + 9, kc_lower=last.ma15 - 4)
    assert ok(bars)[1] == "FORBIDDEN_OVEREXTENDED"


def test_low_volume_under_half_of_20_bar_average():
    assert ok(_with_last(trend_bars(), volume=49.0))[1] == "FORBIDDEN_LOW_VOLUME"
    assert ok(_with_last(trend_bars(), volume=51.0))[0]


def test_two_consecutive_long_wick_bars_blocked_but_one_is_fine():
    bars = trend_bars()
    wick = lambda b: replace(b, high=b.high + 1.0, low=b.low - 1.0)
    bars[-1] = wick(bars[-1])
    assert ok(bars)[0]
    bars[-2] = wick(bars[-2])
    assert ok(bars)[1] == "FORBIDDEN_LONG_WICKS"


def test_cooldown_applies_both_directions():
    assert ok(trend_bars(), state=SwingState("LONG", 3))[1] == "FORBIDDEN_COOLDOWN"
    assert ok(trend_bars(), state=SwingState("SHORT", 4))[0]


def test_breakout_exempt_from_band_and_squeeze_but_not_volume_wick_or_ma5():
    assert ok(breakout_bars(), breakout=True)[0]
    assert not ok(breakout_bars())[0]                      # a MA-cross entry would hit CHASE
    assert ok(_with_last(breakout_bars(), volume=10.0), breakout=True)[1] == "FORBIDDEN_LOW_VOLUME"
    bars = breakout_bars()
    bars[-1] = replace(bars[-1], ma5=bars[-2].ma5 - 0.2)
    assert ok(bars, breakout=True)[1] == "FORBIDDEN_MA5_SLOPING_DOWN"
