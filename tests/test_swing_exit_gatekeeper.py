import pytest

from core.swing import Bar, ExitGatekeeper, Position, floor_roe


def bar(o=100, c=100, h=None, l=None, v=100, ma5=100, ma15=100, up=110, lo=90, atr=2, closed=True):
    return Bar(o, h if h is not None else max(o, c), l if l is not None else min(o, c),
               c, v, ma5, ma15, up, lo, atr, closed)


@pytest.mark.parametrize("peak,expected", [
    (4.9, None), (5.0, None), (7.0, None), (9.0, None), (9.9, None), (10.0, 5.0), (14.8, 5.0), (15.0, 10.0), (19.9, 10.0), (20.0, 15.0),
    (25.0, 20.0), (30.0, 25.0),
])
def test_ladder_boundaries(peak, expected):
    assert floor_roe(peak) == expected


def test_ratchet_triggers_at_or_below_floor_only_when_armed():
    g = ExitGatekeeper.evaluate
    assert g(Position("LONG", 5, 12.0, 5.0), bar(), 100).reason == "RATCHET_PROFIT_LOCK"
    assert g(Position("LONG", 5, 12.0, 5.1), bar(), 100) is None
    assert g(Position("LONG", 5, 4.9, 0.0), bar(), 100) is None  # not armed


@pytest.mark.parametrize("held", [0, 1, 2])
def test_breathing_window_blocks_ordinary_exits(held):
    weak = bar(o=100, c=99, h=100.5, l=97, ma5=99, ma15=99.5)  # close under midline, wick
    assert ExitGatekeeper.evaluate(Position("LONG", held, 0.0, -3.0), weak, 99) is None
    assert ExitGatekeeper.evaluate(Position("SHORT", held, 0.0, -3.0),
                                   bar(o=100, c=101, ma15=100.5), 101) is None


@pytest.mark.parametrize("held", [0, 1, 2])
def test_breathing_window_exemptions(held):
    g = ExitGatekeeper.evaluate
    assert g(Position("LONG", held, 10.5, 4.0), bar(), 100).reason == "RATCHET_PROFIT_LOCK"
    dump = bar(o=104, c=99, ma15=101, atr=2, closed=False)  # body 5 > 1.8*2, breaks midline
    assert g(Position("LONG", held, 0.0, -2.0), bar(), 99, live_bar=dump,
             prior_bars=[bar()]).reason.startswith("EXTREME_DUMP")
    squeeze = bar(o=96, c=102, ma15=100, atr=2, closed=False)
    assert g(Position("SHORT", held, 0.0, -2.0), bar(), 102, live_bar=squeeze,
             prior_bars=[bar()]).reason.startswith("EXTREME_SQUEEZE")


def test_structure_exit_uses_close_only_after_window():
    g = ExitGatekeeper.evaluate
    wick = bar(o=100, c=100.5, l=97, ma15=100)  # wick pierces midline, close above
    assert g(Position("LONG", 3, 0.0, 0.5), wick, 100.5) is None
    assert g(Position("LONG", 3, 0.0, -1.0), bar(o=100, c=99, ma15=100), 99).reason == "STRUCTURE_EXIT_BELOW_MIDLINE"
    assert g(Position("SHORT", 3, 0.0, -1.0), bar(o=100, c=101, ma15=100), 101).reason == "STRUCTURE_EXIT_ABOVE_MIDLINE"
    assert g(Position("LONG", 3, 0.0, -1.0), bar(o=100, c=99, ma15=100, closed=False), 99) is None
