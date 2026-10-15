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


# ---------- breathing window (bars_held < 3) ----------
HELD = (0, 1, 2)


@pytest.mark.parametrize("held", HELD)
def test_window_blocks_micro_loss_ma_cross_band_touch_and_wicks(held):
    live = bar(o=100, c=99, h=110.5, l=95.0, ma5=98, ma15=100, closed=False)  # band touch + long wick
    closed_below_mid = bar(c=99, ma15=100)
    assert evaluate_exit(Position("LONG", held, 2.0, -3.0), closed_below_mid, 99, live, PRIOR) is None
    assert evaluate_exit(Position("SHORT", held, 2.0, -3.0), bar(c=101, ma15=100), 101, live, PRIOR) is None


def test_structure_exit_resumes_at_bar_3_on_close_only():
    assert evaluate_exit(Position("LONG", 3, 2.0, -1.0), bar(c=99, ma15=100), 99).reason.startswith("STRUCTURE")
    wick_only = bar(o=101, c=100.5, l=95, ma15=100)  # deep wick, close above midline
    assert evaluate_exit(Position("LONG", 3, 2.0, 0.0), wick_only, 100.5) is None


@pytest.mark.parametrize("held", HELD)
def test_window_allows_ratchet_lock_with_profit(held):
    d = evaluate_exit(Position("LONG", held, 12.0, 4.0), bar(), 100.0)
    assert d.reason == "RATCHET_PROFIT_LOCK" and d.order_type == "LIMIT"
    assert evaluate_exit(Position("LONG", held, 12.0, 6.0), bar(), 100.0) is None
    assert evaluate_exit(Position("SHORT", held, 9.9, 0.0), bar(), 100.0) is None  # lock not active


@pytest.mark.parametrize("held", HELD)
def test_window_allows_extreme_dump_and_squeeze(held):
    dump = bar(o=106, c=101, ma15=104, closed=False)  # body 5 > 1.8*2, below midline
    assert evaluate_exit(Position("LONG", held, 0.0, -2.0), bar(), 101, dump, PRIOR).reason == "EXTREME_DUMP_BREAKS_MIDLINE"
    squeeze = bar(o=94, c=99, ma15=96, closed=False)
    assert evaluate_exit(Position("SHORT", held, 0.0, -2.0), bar(), 99, squeeze, PRIOR).reason == "EXTREME_SQUEEZE_BREAKS_MIDLINE"


def test_window_dump_needs_body_and_midline_break():
    assert true_reversal("LONG", bar(o=106, c=103, ma15=104, closed=False), PRIOR) is None   # body 3 < 3.6
    assert true_reversal("LONG", bar(o=110, c=105, ma15=104, closed=False), PRIOR) is None   # still above midline
    assert true_reversal("LONG", bar(o=108, c=108.5, h=114, l=107.5, v=300), PRIOR) is None  # wick exhaustion no longer an exit


def test_closed_extreme_bar_after_entry_exits_within_window():
    dump = bar(o=106, c=101, ma15=104)
    assert evaluate_exit(Position("LONG", 1, 0.0, -2.0), dump, 101, None, PRIOR + [dump]) is not None
    assert evaluate_exit(Position("LONG", 0, 0.0, -2.0), dump, 101, None, PRIOR + [dump]) is None  # stale pre-entry bar


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
