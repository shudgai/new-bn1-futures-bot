import asyncio
from types import SimpleNamespace

import pytest

from core.swing import Bar, Position, evaluate_exit
from core.swing.account_guard import (
    BREAKER_REASON, breaker_active, breaker_reason, enforce_account_breaker, unrealized_pnl,
)
from core.swing.pivot import true_reversal


def bar(o=100, c=100, h=None, l=None, v=100, ma5=100, ma15=100, up=110, lo=90, atr=2, closed=True):
    return Bar(o, h if h is not None else max(o, c), l if l is not None else min(o, c),
               c, v, ma5, ma15, up, lo, atr, closed)


PRIOR = [bar() for _ in range(10)]


# ---------- bar 0 ----------
def test_bar0_blocks_micro_loss_ma_cross_and_band_touch():
    live = bar(o=100, c=99, h=110.5, l=98.8, ma5=98, ma15=100, closed=False)  # touches upper, small loss
    assert evaluate_exit(Position("LONG", 0, 2.0, -3.0), bar(c=99, ma15=100), 99, live, PRIOR) is None
    assert evaluate_exit(Position("SHORT", 0, 2.0, -3.0), bar(c=101, ma15=100), 101, live, PRIOR) is None


def test_bar0_allows_ratchet_lock_with_profit():
    d = evaluate_exit(Position("LONG", 0, 12.0, 4.0), bar(), 100.0)
    assert d.reason == "RATCHET_PROFIT_LOCK" and d.order_type == "LIMIT"
    assert evaluate_exit(Position("LONG", 0, 12.0, 6.0), bar(), 100.0) is None
    assert evaluate_exit(Position("SHORT", 0, 9.9, 0.0), bar(), 100.0) is None  # lock not active


def test_bar0_long_exhaustion_wick_allowed():
    live = bar(o=108, c=108.5, h=114, l=107.5, v=300, closed=False)
    assert true_reversal("LONG", live, PRIOR) == "TRUE_PEAK_EXHAUSTION"
    assert evaluate_exit(Position("LONG", 0, 1.0, -1.0), bar(), 108, live, PRIOR).reason == "TRUE_PEAK_EXHAUSTION"


def test_bar0_short_exhaustion_wick_allowed():
    live = bar(o=92, c=91.5, h=92.5, l=86, v=300, closed=False)
    assert evaluate_exit(Position("SHORT", 0, 1.0, -1.0), bar(), 92, live, PRIOR).reason == "TRUE_TROUGH_EXHAUSTION"


def test_bar0_wick_without_volume_or_size_is_not_true_pivot():
    assert true_reversal("LONG", bar(o=108, c=108.5, h=114, l=107.5, v=100), PRIOR) is None
    assert true_reversal("LONG", bar(o=108, c=108.5, h=109.5, l=107.5, v=300), PRIOR) is None
    assert true_reversal("LONG", bar(o=108, c=108.5, h=114, l=107.5, v=300, up=120), PRIOR) is None  # not at ceiling


def test_bar0_extreme_body_engulf_both_sides():
    dump = bar(o=106, c=101, ma15=104, closed=False)  # body 5 > 1.8*2
    assert evaluate_exit(Position("LONG", 0, 0.0, -2.0), bar(), 101, dump, PRIOR).reason == "TRUE_PEAK_DUMP_ENGULF"
    squeeze = bar(o=94, c=99, ma15=96, closed=False)
    assert evaluate_exit(Position("SHORT", 0, 0.0, -2.0), bar(), 99, squeeze, PRIOR).reason == "TRUE_TROUGH_SQUEEZE_ENGULF"
    small = bar(o=106, c=103, ma15=104, closed=False)  # body 3 < 3.6
    assert true_reversal("LONG", small, PRIOR) is None


# ---------- no per-position hard stop ----------
def test_deep_position_loss_never_exits_without_structure():
    # -50% ROE, still above midline structure: no fixed stop exists
    assert evaluate_exit(Position("LONG", 3, 0.0, -50.0), bar(c=101, ma15=100), 101) is None


def test_hard_stop_module_removed():
    with pytest.raises(ImportError):
        __import__("core.services.exits.hard_stop_service")


# ---------- account breaker ----------
def test_breaker_reason_thresholds():
    assert breaker_reason(950, 1000, 5.0) == "DAILY_LOSS_LIMIT"
    assert breaker_reason(951, 1000, 5.0) is None
    assert breaker_reason(499, 1000, 0, equity_floor=500) == "EQUITY_FLOOR"
    assert breaker_reason(float("nan"), 1000, 5.0, 500) is None
    assert breaker_reason(100, 0, 5.0) is None


def test_unrealized_pnl_long_short():
    pos = {"A": {"side": "LONG", "entry_price": 100, "qty": 2}, "B": {"side": "SHORT", "entry_price": 50, "qty": 1}}
    assert unrealized_pnl(pos, {"A": 95, "B": 52}) == pytest.approx(-12)
    assert unrealized_pnl(pos, {}) == 0.0


class FakeAccount:
    def __init__(self, wallet, start, positions):
        self.wallet, self.daily_start_balance, self.positions = wallet, start, positions
        self.closed, self.logs = [], []

    def get_wallet_balance(self):
        return self.wallet

    def log(self, msg, level="INFO"):
        self.logs.append(msg)

    async def close_position(self, symbol, price, reason, is_manual=False, **kw):
        self.closed.append((symbol, reason))
        self.positions.pop(symbol, None)
        return True


def test_breaker_flattens_all_positions_and_halts():
    pos = {"龙虾/USDT": {"side": "LONG", "entry_price": 100, "qty": 10},
           "X/USDT": {"side": "SHORT", "entry_price": 10, "qty": 1}}
    acc = FakeAccount(1000.0, 1000.0, pos)
    tripped = asyncio.run(enforce_account_breaker(acc, {"龙虾/USDT": 90.0, "X/USDT": 10.0}, 5.0, 0.0, "2026-01-01"))
    assert tripped and not acc.positions and len(acc.closed) == 2
    assert all(r.startswith(BREAKER_REASON) for _, r in acc.closed)
    assert breaker_active(acc, "2026-01-01") and not breaker_active(acc, "2026-01-02")


def test_breaker_does_not_trip_inside_limits():
    acc = FakeAccount(1000.0, 1000.0, {"龙虾/USDT": {"side": "LONG", "entry_price": 100, "qty": 10}})
    assert not asyncio.run(enforce_account_breaker(acc, {"龙虾/USDT": 96.0}, 5.0, 0.0, "d"))
    assert acc.positions and not acc.closed
