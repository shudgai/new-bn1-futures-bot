import pytest

from core.swing import Bar, Position, evaluate_exit
from core.swing.exits import ExitGatekeeper
from core.swing.pivot import true_reversal


def bar(o, c, h=None, l=None, v=100, ma5=100.0, ma15=99.0, atr=2.0):
    return Bar(o, h if h is not None else max(o, c) + 0.1, l if l is not None else min(o, c) - 0.1,
               c, v, ma5, ma15, ma15 + 6, ma15 - 6, atr, True)


def uptrend(n=14):
    return [bar(100 + i, 100.6 + i, ma5=100 + i * 1.0, ma15=99 + i * 0.6) for i in range(n)]


def test_riding_ma5_and_upper_band_never_exits_long():
    bars = uptrend()
    for k in range(5, len(bars)):
        b = bars[k]
        pullback = bar(b.close + 0.4, b.close - 0.3, ma5=b.ma5, ma15=b.ma15)  # small red candle above MA5
        for held in (3, 6, 20):
            assert evaluate_exit(Position("LONG", held, 5.0, 3.0), pullback, pullback.close, None, bars[:k]) is None


def test_riding_ma5_and_lower_band_never_exits_short():
    bars = [bar(200 - i, 199.4 - i, ma5=200 - i, ma15=201 - i * 0.6) for i in range(14)]
    for k in range(5, 14):
        b = bars[k]
        bounce = bar(b.close - 0.4, b.close + 0.3, ma5=b.ma5, ma15=b.ma15)
        assert evaluate_exit(Position("SHORT", 5, 5.0, 3.0), bounce, bounce.close, None, bars[:k]) is None


def test_close_under_midline_exits_only_when_trend_not_intact():
    flat_prev = bar(100, 100, ma5=100, ma15=100)
    broken = bar(100, 98, ma5=99, ma15=99.5)  # both MAs falling, close below both
    assert evaluate_exit(Position("LONG", 5, 2.0, -1.0), broken, 98, None, [flat_prev]).reason.startswith("STRUCTURE")
    intact = bar(100, 100.4, ma5=100.5, ma15=100.2)
    assert ExitGatekeeper.trend_intact("LONG", intact, [flat_prev])


def test_ratchet_lock_still_exits_in_intact_trend():
    b = bars_up = uptrend()[-1]
    d = evaluate_exit(Position("LONG", 5, 12.0, 6.0), b, b.close, None, uptrend()[:-1])
    assert d.reason == "RATCHET_PROFIT_LOCK"


PRIOR = [bar(100, 100.5, ma5=100, ma15=99) for _ in range(10)]


def test_true_peak_climax_needle_long_and_trough_short():
    needle = bar(108, 108.5, h=114, l=107.9, v=250)
    assert true_reversal("LONG", needle, PRIOR) == "TRUE_PEAK_CLIMAX_NEEDLE"
    assert true_reversal("LONG", bar(108, 108.5, h=114, l=107.9, v=150), PRIOR) is None
    assert true_reversal("LONG", bar(108, 108.5, h=108.9, l=107.9, v=250), PRIOR) is None
    trough = bar(92, 91.5, h=92.1, l=86, v=250)
    assert true_reversal("SHORT", trough, PRIOR) == "TRUE_TROUGH_CLIMAX_NEEDLE"


def test_engulfing_dump_and_squeeze():
    dump = bar(106, 102, ma5=104, ma15=102.5)  # body 4 >= 3.6, under MA5, near midline
    assert true_reversal("LONG", dump, PRIOR) == "EXTREME_DUMP_BREAKS_MIDLINE"
    assert true_reversal("LONG", bar(106, 102.5, ma5=101, ma15=100), PRIOR) is None  # still above MA5
    squeeze = bar(94, 98, ma5=96, ma15=97.5)
    assert true_reversal("SHORT", squeeze, PRIOR) == "EXTREME_SQUEEZE_BREAKS_MIDLINE"


@pytest.mark.parametrize("held", [0, 1, 5])
def test_true_peak_exits_even_in_intact_trend_after_bar0(held):
    needle = bar(108, 108.5, h=114, l=107.9, v=250, ma5=105, ma15=102)
    live = Bar(*[getattr(needle, f) for f in ("open", "high", "low", "close", "volume", "ma5", "ma15", "kc_upper", "kc_lower", "atr")], False)
    d = evaluate_exit(Position("LONG", held, 0.0, 1.0), PRIOR[-1], 108.5, live, PRIOR)
    assert d and d.reason == "TRUE_PEAK_CLIMAX_NEEDLE"
