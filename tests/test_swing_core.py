import pytest

from core.swing import (
    Bar, Position, SwingState, evaluate_dump_reversal, evaluate_entry, evaluate_exit, floor_roe,
)


def bar(o=100, c=100, h=None, l=None, v=100, ma5=100, ma15=100, up=110, lo=90, atr=2, closed=True):
    return Bar(o, h if h is not None else max(o, c), l if l is not None else min(o, c),
               c, v, ma5, ma15, up, lo, atr, closed)


@pytest.mark.parametrize("peak,expected", [
    (9.99, None), (10.0, 5.0), (12.0, 5.0), (14.99, 5.0), (15.0, 10.0), (17.0, 10.0),
    (19.99, 10.0), (23.0, 15.0), (25.0, 20.0), (30.0, 25.0), (34.99, 25.0),
])
def test_floor_roe_ladder(peak, expected):
    assert floor_roe(peak) == expected


def test_ratchet_locks_below_floor_with_limit_offset():
    b = bar(c=100)
    d = evaluate_exit(Position("LONG", 3, 12.0, 4.9), b, 100.0)
    assert d.reason == "RATCHET_PROFIT_LOCK" and d.order_type == "LIMIT"
    assert d.limit_price == pytest.approx(99.75)
    assert evaluate_exit(Position("LONG", 3, 12.0, 5.0), b, 100.0) is None
    d = evaluate_exit(Position("SHORT", 3, 23.0, 14.0), b, 100.0)
    assert d.limit_price == pytest.approx(100.25)
    assert evaluate_exit(Position("LONG", 3, 17.0, 9.9), b, 100.0) is not None
    assert evaluate_exit(Position("LONG", 3, 17.0, 10.0), b, 100.0) is None


def test_no_exit_on_entry_bar():
    assert evaluate_exit(Position("LONG", 0, 3.0, -4.0), bar(c=80, ma15=100), 80) is None


def test_structure_exit_only_before_ratchet_and_closed_bar():
    assert evaluate_exit(Position("LONG", 1, 3.0, 1.0), bar(c=99, ma15=100), 99).reason.startswith("STRUCTURE")
    assert evaluate_exit(Position("LONG", 1, 3.0, 1.0), bar(c=99, ma15=100, closed=False), 99) is None
    assert evaluate_exit(Position("SHORT", 1, 3.0, 1.0), bar(c=101, ma15=100), 101) is not None
    # ratchet active: midline break alone does not exit
    assert evaluate_exit(Position("LONG", 5, 12.0, 8.0), bar(c=99, ma15=100), 99) is None


def _cross_up_bars(chop=False):
    bars = []
    for i in range(10):
        c = 99 if (chop and i % 2) == 0 else 101 if chop else 98
        bars.append(bar(c=c, ma5=98, ma15=99, up=110, lo=90))
    bars[-1] = bar(c=101, ma5=100.0, ma15=99.5, up=110, lo=90)
    bars[-2] = bar(c=98, ma5=99.0, ma15=99.0 - 0.0, up=110, lo=90)
    bars[-2] = bar(c=98, ma5=98.9, ma15=99.0)
    return bars


def test_ma_cross_long_accepted_in_clean_trend():
    sig = evaluate_entry(_cross_up_bars())
    assert sig and sig.side == "LONG" and sig.mode == "MA_SWING"


def test_whipsaw_rejects_entry():
    bars = _cross_up_bars(chop=True)
    bars[-1] = bar(c=101, ma5=100.0, ma15=99.5)
    bars[-2] = bar(c=98, ma5=98.9, ma15=99.0)
    assert evaluate_entry(bars) is None


def test_outer_band_blocks_chasing_long():
    bars = _cross_up_bars()
    bars[-1] = bar(c=101, ma5=100.0, ma15=99.5, up=102, lo=95)  # kc_pos ~0.86
    assert bars[-1].kc_pos > 0.8
    assert evaluate_entry(bars) is None


def test_cooldown_blocks_opposite_only():
    bars = _cross_up_bars()
    assert evaluate_entry(bars, SwingState("SHORT", 2)) is None
    assert evaluate_entry(bars, SwingState("SHORT", 4)) is not None
    assert evaluate_entry(bars, SwingState("LONG", 1)) is not None


def test_breakout_long_needs_body_and_volume():
    bars = [bar(c=100, v=100) for _ in range(10)]
    bars.append(bar(o=108, c=112, up=110, atr=3, v=150))
    sig = evaluate_entry(bars)
    assert sig and sig.mode == "BREAKOUT_MOMENTUM" and sig.side == "LONG" and sig.entry_at == "NEXT_OPEN"
    assert evaluate_entry(bars[:-1] + [bar(o=108, c=112, up=110, atr=3, v=120)]) is None
    assert evaluate_entry(bars[:-1] + [bar(o=110, c=112, up=110, atr=3, v=150)]) is None


def test_breakout_short():
    bars = [bar(c=100, v=100) for _ in range(10)]
    bars.append(bar(o=92, c=88, lo=90, atr=3, v=150))
    assert evaluate_entry(bars).side == "SHORT"


def test_no_signal_in_noise():
    assert evaluate_entry([bar(), bar()]) is None
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
