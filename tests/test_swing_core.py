import pytest

from core.swing import (
    Bar, Position, SwingState, evaluate_dump_reversal, evaluate_entry, evaluate_exit, floor_roe,
)


def bar(o=100, c=100, h=None, l=None, v=100, ma5=100, ma15=100, up=110, lo=90, atr=2, closed=True):
    return Bar(o, h if h is not None else max(o, c), l if l is not None else min(o, c),
               c, v, ma5, ma15, up, lo, atr, closed)


@pytest.mark.parametrize("peak,expected", [
    (4.99, None), (5.0, 2.0), (6.99, 2.0), (7.0, 3.0), (8.99, 3.0), (9.0, 4.5), (9.5, 4.7), (9.9, 4.86),
    (10.0, 5.0), (12.0, 5.0), (14.99, 5.0), (15.0, 10.0), (19.99, 10.0), (20.0, 15.0), (24.99, 15.0),
    (25.0, 20.0), (30.0, 25.0), (34.99, 25.0),
])
def test_floor_roe_ladder(peak, expected):
    assert floor_roe(peak) == pytest.approx(expected)


def test_ratchet_locks_below_floor_with_limit_offset():
    b = bar(c=100)
    d = evaluate_exit(Position("LONG", 3, 12.0, 4.9), b, 100.0)
    assert d.reason == "RATCHET_PROFIT_LOCK" and d.order_type == "LIMIT"
    assert d.limit_price == pytest.approx(99.75)
    assert evaluate_exit(Position("LONG", 3, 12.0, 5.0), b, 100.0) is not None  # <= floor locks
    d = evaluate_exit(Position("SHORT", 3, 23.0, 14.0), b, 100.0)
    assert d.limit_price == pytest.approx(100.25)
    assert evaluate_exit(Position("LONG", 3, 17.0, 9.9), b, 100.0) is not None
    assert evaluate_exit(Position("LONG", 3, 17.0, 10.1), b, 100.0) is None


def test_no_exit_on_entry_bar():
    assert evaluate_exit(Position("LONG", 0, 3.0, -4.0), bar(c=80, ma15=100), 80) is None


def test_structure_exit_only_before_ratchet_and_closed_bar():
    assert evaluate_exit(Position("LONG", 3, 3.0, 1.0), bar(c=99, ma15=100), 99).reason.startswith("STRUCTURE")
    assert evaluate_exit(Position("LONG", 3, 3.0, 1.0), bar(c=99, ma15=100, closed=False), 99) is None
    assert evaluate_exit(Position("SHORT", 3, 3.0, 1.0), bar(c=101, ma15=100), 101) is not None
    # inside the 3-bar breathing window a plain midline close does not exit
    for held in (0, 1, 2):
        assert evaluate_exit(Position("LONG", held, 3.0, 1.0), bar(c=99, ma15=100), 99) is None
    # ratchet active: midline break alone does not exit
    assert evaluate_exit(Position("LONG", 5, 12.0, 6.0), bar(c=99, ma15=100), 99) is None


def trend_bars(side="LONG", n=25):
    """Clean trend with a MA5/MA15 cross on the last bar. ATR 1, KC half-width 4."""
    sign = 1 if side == "LONG" else -1
    out = []
    for i in range(n):
        mid = 100 + sign * 0.05 * i
        c = mid + sign * 0.4
        o = c - sign * 0.3
        h, l = max(o, c) + 0.1, min(o, c) - 0.1
        ma5 = mid - sign * 0.1
        out.append(Bar(o, h, l, c, 100, ma5, mid, mid + 4, mid - 4, 1.0, True))
    last = out[-1]
    out[-1] = Bar(last.open, last.high, last.low, last.close, 100, last.ma15 + sign * 0.1,
                  last.ma15, last.kc_upper, last.kc_lower, 1.0, True)
    return out


def _with_last(bars, **kw):
    from dataclasses import replace
    return bars[:-1] + [replace(bars[-1], **kw)]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ma_cross_accepted_in_clean_trend(side):
    sig = evaluate_entry(trend_bars(side))
    assert sig and sig.side == side and sig.mode == "MA_SWING" and sig.entry_at == "NEXT_OPEN"


def test_unclosed_signal_bar_never_enters():
    assert evaluate_entry(_with_last(trend_bars(), is_closed=False)) is None


def test_whipsaw_rejects_entry():
    from dataclasses import replace
    bars = trend_bars()
    for i, off in zip(range(-9, -1), [-0.4, 0.4] * 4):
        bars[i] = replace(bars[i], close=bars[i].ma15 + off)
    assert evaluate_entry(bars) is None


def test_outer_band_blocks_chasing_long():
    bars = _with_last(trend_bars(), kc_upper=101.5, kc_lower=95.0)  # kc_pos > 0.8
    assert bars[-1].kc_pos > 0.8
    assert evaluate_entry(bars) is None


def test_insufficient_room_blocks_entry():
    # kc_pos 0.62 is legal but only 1.2 ATR remains to the upper band
    bars = _with_last(trend_bars(), kc_upper=trend_bars()[-1].close + 1.2, kc_lower=trend_bars()[-1].close - 2.0)
    assert 0.4 <= bars[-1].kc_pos <= 0.7
    assert evaluate_entry(bars) is None


def test_cooldown_blocks_both_sides_until_elapsed():
    bars = trend_bars()
    assert evaluate_entry(bars, SwingState("SHORT", 2)) is None
    assert evaluate_entry(bars, SwingState("SHORT", 4)) is not None
    assert evaluate_entry(bars, SwingState("LONG", 1)) is None
    assert evaluate_entry(bars, SwingState("LONG", 4)) is not None


def breakout_bars(side="LONG", vol=250, body=6.0):
    from dataclasses import replace
    bars = trend_bars(side)[:-1] + [trend_bars(side)[-1]]
    last = bars[-1]
    sign = 1 if side == "LONG" else -1
    o = last.kc_upper - 1.0 if side == "LONG" else last.kc_lower + 1.0
    c = o + sign * body
    bars[-1] = replace(last, open=o, close=c, high=max(o, c) + 0.2, low=min(o, c) - 0.2, volume=vol,
                       ma5=bars[-2].ma5 + sign * 0.5, ma15=bars[-2].ma15 + sign * 0.05)
    return bars


def test_breakout_long_needs_body_and_volume():
    sig = evaluate_entry(breakout_bars())
    assert sig and sig.mode == "BREAKOUT_MOMENTUM" and sig.side == "LONG" and sig.entry_at == "NEXT_OPEN"
    assert evaluate_entry(breakout_bars(vol=190)) is None      # < 2.0x average volume
    assert evaluate_entry(breakout_bars(body=1.4)) is None     # body < 1.5 ATR


def test_breakout_short():
    assert evaluate_entry(breakout_bars("SHORT")).side == "SHORT"


def test_no_signal_in_noise():
    assert evaluate_entry([bar(), bar()]) is None  # too little history
    assert evaluate_entry([bar(), bar(closed=False)]) is None


def test_dump_flush_closes_long_and_flips_short():
    bars = [bar(o=100, c=101) for _ in range(5)] + [bar(o=101, c=95, ma5=100, ma15=100, atr=3)]
    sig = evaluate_dump_reversal("LONG", bars)
    assert sig and sig.side == "SHORT" and sig.mode == "DUMP_FLUSH_REVERSAL"
    assert evaluate_dump_reversal("SHORT", bars) is None


def test_dump_by_engulfing_three_bulls():
    bulls = [bar(o=100, c=101), bar(o=101, c=102), bar(o=102, c=103)]
    last = bar(o=103.5, c=99.5, ma5=101, ma15=101, atr=5)
    assert evaluate_dump_reversal("LONG", bulls + [last]).side == "SHORT"


def test_small_red_or_holding_above_ma_not_a_dump():
    bars = [bar(o=100, c=101) for _ in range(5)]
    assert evaluate_dump_reversal("LONG", bars + [bar(o=100.8, c=100.2, atr=3)]) is None
    assert evaluate_dump_reversal("LONG", bars + [bar(o=101, c=95, ma5=90, ma15=90, atr=3)]) is None
