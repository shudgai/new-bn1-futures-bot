import math
import random

from core.swing import Bar, Position, evaluate_entry, evaluate_exit, floor_roe
from core.swing.entry import is_whipsaw


def _series(rng, n=40):
    price, bars = 100.0, []
    for _ in range(n):
        o = price
        c = o + rng.gauss(0, 1.5)
        atr = abs(rng.gauss(1.5, 0.5)) + 0.01
        mid = (o + c) / 2 + rng.gauss(0, 0.5)
        width = abs(rng.gauss(4, 1.5)) + 0.5
        bars.append(Bar(o, max(o, c) + abs(rng.gauss(0, .5)), min(o, c) - abs(rng.gauss(0, .5)), c,
                        abs(rng.gauss(100, 40)), mid + rng.gauss(0, .5), mid,
                        mid + width, mid - width, atr))
        price = c
    return bars


def test_entry_invariants_random():
    rng = random.Random(7)
    for _ in range(3000):
        bars = _series(rng)
        sig = evaluate_entry(bars)
        if sig is None:
            continue
        last = bars[-1]
        assert sig.side in ("LONG", "SHORT")
        if sig.mode == "MA_SWING":
            assert not is_whipsaw(bars)
            pos = last.kc_pos
            assert pos is not None
            if sig.side == "LONG":
                assert pos <= 0.70 and last.close < last.kc_upper
            else:
                assert pos >= 0.30 and last.close > last.kc_lower


def test_exit_never_on_entry_bar_and_ratchet_floor():
    rng = random.Random(11)
    for _ in range(3000):
        bar = _series(rng, 3)[-1]
        peak = rng.uniform(-5, 60)
        cur = rng.uniform(-10, peak)
        side = rng.choice(["LONG", "SHORT"])
        d0 = evaluate_exit(Position(side, 0, peak, cur), bar, bar.close)
        f0 = floor_roe(peak)
        if f0 is not None and cur < f0:
            assert d0 is not None and d0.reason == "RATCHET_PROFIT_LOCK"
        else:
            assert d0 is None  # bar 0: only the ratchet may exit (no live bar given)
        d = evaluate_exit(Position(side, 3, peak, cur), bar, bar.close)
        fl = floor_roe(peak)
        if fl is not None and cur < fl:
            assert d is not None
        if fl is not None:
            assert fl == float((int(peak // 5) * 5) - 5) if peak >= 25 else fl >= 2.0
