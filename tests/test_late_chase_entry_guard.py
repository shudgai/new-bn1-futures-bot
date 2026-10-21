"""Late-chase guard: continuation / 2-bar breakout must not chase extended moves."""

from core.gates.breakout_continuation_entry_gate import (
    CONFIRMED_2BAR_BREAKOUT,
    CONTINUATION_OUTSIDE_KC,
    INSTANT_BREAKOUT_INSIDE,
    BreakoutContinuationEntryGate as Gate,
)


def _bars(closes, upper=101.0, lower=99.0, atr=1.0):
    rows = []
    for close in closes:
        rows.append({
            "open": close - 0.1, "high": close + 0.2, "low": close - 0.2,
            "close": close, "atr": atr,
        })
    uppers = [upper] * len(rows)
    lowers = [lower] * len(rows)
    return rows, uppers, lowers


def _cand(entry_type, action, price, ma5, upper=101.0, lower=99.0):
    return {
        "type": entry_type, "action": action, "price": price,
        "ma5_current": ma5, "kc_upper": upper, "kc_lower": lower,
    }


def test_fresh_continuation_long_allowed():
    rows, up, lo = _bars([100.0, 100.5, 101.2, 101.4])
    cand = _cand(CONTINUATION_OUTSIDE_KC, "OPEN_LONG", 101.4, 100.8)
    assert Gate.late_chase_block_reason(cand, rows, up, lo) is None


def test_stale_continuation_long_blocked_after_three_bars_outside():
    rows, up, lo = _bars([100.0, 101.1, 101.2, 101.3, 101.4])
    cand = _cand(CONTINUATION_OUTSIDE_KC, "OPEN_LONG", 101.4, 101.0)
    reason = Gate.late_chase_block_reason(cand, rows, up, lo)
    assert reason and reason.startswith("LATE_CHASE_STALE_CONTINUATION")


def test_band_extension_blocks_short():
    rows, up, lo = _bars([100.0, 99.5, 98.3])
    cand = _cand(CONTINUATION_OUTSIDE_KC, "OPEN_SHORT", 98.3, 98.9)
    reason = Gate.late_chase_block_reason(cand, rows, up, lo)
    assert reason and reason.startswith("LATE_CHASE_BAND_EXTENSION")


def test_ma5_distance_blocks_2bar_breakout():
    rows, up, lo = _bars([100.0, 101.2, 101.4])
    cand = _cand(CONFIRMED_2BAR_BREAKOUT, "OPEN_LONG", 101.4, 100.2)
    reason = Gate.late_chase_block_reason(cand, rows, up, lo)
    assert reason and reason.startswith("LATE_CHASE_MA5_DISTANCE")


def test_runaway_tick_blocks_at_final_revalidation():
    rows, up, lo = _bars([100.0, 101.2, 101.3])
    cand = _cand(CONFIRMED_2BAR_BREAKOUT, "OPEN_LONG", 101.3, 101.0)
    assert Gate.late_chase_block_reason(cand, rows, up, lo) is None
    reason = Gate.late_chase_block_reason(cand, rows, up, lo, tick_price=101.8)
    assert reason and reason.startswith("LATE_CHASE_BAND_EXTENSION")


def test_unguarded_type_untouched():
    rows, up, lo = _bars([100.0, 103.0, 104.0])
    cand = _cand(INSTANT_BREAKOUT_INSIDE, "OPEN_LONG", 104.0, 100.0)
    assert Gate.late_chase_block_reason(cand, rows, up, lo) is None


def test_missing_atr_fails_closed():
    rows = [{"close": 101.2}, {"close": 101.3}]
    up, lo = [101.0, 101.0], [99.0, 99.0]
    cand = _cand(CONTINUATION_OUTSIDE_KC, "OPEN_LONG", 101.3, 101.0)
    assert Gate.late_chase_block_reason(cand, rows, up, lo) == "LATE_CHASE_DATA_INVALID"
