from core.swing import Bar
from core.swing.entry import _breakout
from core.swing.ratchet import floor_roe


def flat(i=0):
    return Bar(100, 100.3, 99.7, 100, 100, 100, 100, 104, 96, 1.0, True)


def sig(prev_outside, wick=0.1, short=False):
    bars = [flat() for _ in range(25)]
    if prev_outside:
        bars[-1] = (Bar(95.3, 95.4, 94.6, 95, 100, 100, 100, 104, 96, 1.0, True) if short
                    else Bar(104.7, 105.4, 104.6, 105, 100, 100, 100, 104, 96, 1.0, True))
    if not short:
        last = Bar(100.0, 106 + wick, 99.9, 106.0, 300, 100.5, 100, 104, 96, 1.0, True)
    else:
        last = Bar(100.0, 100.1, 94 - wick, 94.0, 300, 99.5, 100, 104, 96, 1.0, True)
    bars.append(last)
    return _breakout(bars)


def test_upper_wick_over_25pct_rejected():
    assert sig(True, wick=0.1) is not None
    assert sig(True, wick=2.0) is None          # wick 2.0 > 25% of body 6


def test_lower_wick_over_25pct_rejected_for_short():
    assert sig(True, wick=0.1, short=True) is not None
    assert sig(True, wick=2.0, short=True) is None


def test_first_single_bar_break_after_flat_is_trap():
    assert sig(False) is None                   # first break after 5 flat bars
    assert sig(True) is not None                # 2nd bar holding outside confirms


def test_compact_ratchet_ladder():
    assert floor_roe(9.9) is None
    assert floor_roe(10.0) == 7.0 and floor_roe(14.99) == 7.0
    assert floor_roe(15.0) == 12.0 and floor_roe(19.99) == 12.0
    assert floor_roe(20.0) == 15.0 and floor_roe(26.0) == 21.0
