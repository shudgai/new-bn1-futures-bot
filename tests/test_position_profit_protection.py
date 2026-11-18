import asyncio

import pandas as pd
import pytest

from core.services.exits.position_profit_protection import (
    evaluate_and_persist_position_protection,
    tiered_profit_stop,
)
from core.services.exits import position_profit_protection
from core.services.exits.priority_exit_manager import (
    _valid_authorization,
    evaluate_and_execute_exit,
    evaluate_priority_exit,
)


LIVE_STAMP_MS = 2_000_000_040_000 // 60_000 * 60_000
BASE_BARS = [
    {
        "timestamp": LIVE_STAMP_MS - 180_000,
        "open": 99.5, "high": 100.2, "low": 99.0, "close": 100.0,
        "ma5": 99.8, "atr": 1.0, "is_closed": True,
    },
    {
        "timestamp": LIVE_STAMP_MS - 120_000,
        "open": 100.0, "high": 100.3, "low": 99.8, "close": 100.1,
        "ma5": 99.9, "atr": 1.0, "is_closed": True,
    },
    {
        "timestamp": LIVE_STAMP_MS - 60_000,
        "open": 100.0, "high": 100.2, "low": 99.5, "close": 100.1,
        "ma5": 99.9, "atr": 1.0, "is_closed": True,
    },
]


def _bar(timestamp, opening, high, low, close, ma5=100.0, atr=1.0, closed=True):
    return {
        "timestamp": timestamp,
        "open": opening,
        "high": high,
        "low": low,
        "close": close,
        "ma5": ma5,
        "atr": atr,
        "is_closed": closed,
    }


def _frame(closed_bars, live_timestamp):
    live = _bar(
        live_timestamp, 100.0, 100.2, 99.8, 100.0,
        ma5=100.0, closed=False,
    )
    frame = pd.DataFrame([*closed_bars, live])
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["snapshot_ms"] = live_timestamp + 20_000
    return frame


def _position(side, entry, opened_timestamp):
    return {
        "symbol": "LOBSTER/USDT",
        "side": side,
        "entry_price": entry,
        "open_timestamp": opened_timestamp,
        "qty": 1.0,
        "leverage": 1.0,
        "margin": 100.0,
    }


def _evaluate(db_path, position, bars, live_timestamp, quote):
    return evaluate_and_persist_position_protection(
        "LOBSTER/USDT",
        position,
        _frame(bars, live_timestamp),
        quote,
        now=(live_timestamp + 20_000) / 1000,
        db_path=db_path,
    )


@pytest.mark.parametrize(
    ("peak", "previous", "expected_stop", "expected_tier"),
    [
        (7.9, None, None, 0),
        (8.0, None, 5.0, 1),
        (9.0, None, 6.0, 1),
        (13.0, None, 10.0, 2),
        (18.0, None, 15.0, 3),
        (23.0, None, 20.0, 4),
        (14.0, 12.0, 12.0, 2),
    ],
)
def test_tiered_profit_stop_boundaries_and_monotonicity(
    peak, previous, expected_stop, expected_tier,
):
    assert tiered_profit_stop(peak, previous) == (expected_stop, expected_tier)


@pytest.mark.parametrize(
    ("side", "entry", "quote", "first", "second"),
    [
        (
            "LONG", 95.0, 99.0,
            _bar(LIVE_STAMP_MS, 100.0, 100.1, 99.6, 99.7, ma5=99.8),
            _bar(LIVE_STAMP_MS + 60_000, 99.6, 99.7, 98.9, 99.0, ma5=99.4),
        ),
        (
            "SHORT", 105.0, 101.0,
            _bar(LIVE_STAMP_MS, 100.0, 100.4, 99.9, 100.3, ma5=100.2),
            _bar(LIVE_STAMP_MS + 60_000, 100.2, 101.1, 100.1, 101.0, ma5=100.6),
        ),
    ],
)
def test_two_closed_reversal_candles_protect_profit_below_lock_threshold(
    tmp_path, side, entry, quote, first, second,
):
    db_path = tmp_path / "protection.sqlite3"
    position = _position(side, entry, (LIVE_STAMP_MS - 300_000) / 1000)

    initialized = _evaluate(db_path, position, BASE_BARS, LIVE_STAMP_MS, quote)
    assert initialized["status"] == "READY"
    assert initialized["evidence"]["max_net_roi"] < 8.0

    first_closed = _evaluate(
        db_path, position, [*BASE_BARS, first], LIVE_STAMP_MS + 60_000, quote,
    )
    assert first_closed["reason"] is None
    assert first_closed["evidence"]["consecutive_opposite_candles"] == 1

    second_closed = _evaluate(
        db_path, position, [*BASE_BARS, first, second],
        LIVE_STAMP_MS + 120_000, quote,
    )
    assert second_closed["reason"] == "PRIORITY_EXIT_P1_CONSECUTIVE_REVERSAL_PROFIT"
    assert second_closed["evidence"]["current_net_pnl_usdt"] > 0


@pytest.mark.parametrize(
    ("side", "entry", "quote", "strong"),
    [
        (
            "LONG", 95.0, 99.3,
            _bar(LIVE_STAMP_MS, 100.0, 100.1, 98.8, 99.3, ma5=99.7),
        ),
        (
            "SHORT", 105.0, 100.7,
            _bar(LIVE_STAMP_MS, 100.0, 101.2, 99.9, 100.7, ma5=100.3),
        ),
    ],
)
def test_single_strong_closed_reversal_requires_structure_break(
    tmp_path, side, entry, quote, strong,
):
    db_path = tmp_path / "protection.sqlite3"
    position = _position(side, entry, (LIVE_STAMP_MS - 300_000) / 1000)
    _evaluate(db_path, position, BASE_BARS, LIVE_STAMP_MS, quote)

    result = _evaluate(
        db_path,
        position,
        [*BASE_BARS, strong],
        LIVE_STAMP_MS + 60_000,
        quote,
    )
    assert result["reason"] == "PRIORITY_EXIT_P1_STRONG_REVERSAL_PROFIT"


def test_one_closed_reversal_and_duplicate_snapshot_do_not_confirm(tmp_path):
    db_path = tmp_path / "protection.sqlite3"
    position = _position("LONG", 95.0, (LIVE_STAMP_MS - 300_000) / 1000)
    initialized = _evaluate(db_path, position, BASE_BARS, LIVE_STAMP_MS, 99.0)
    one_reversal = _bar(
        LIVE_STAMP_MS, 100.0, 100.1, 99.6, 99.7, ma5=99.8,
    )
    bars = [*BASE_BARS, one_reversal]

    first = _evaluate(db_path, position, bars, LIVE_STAMP_MS + 60_000, 99.0)
    repeated = _evaluate(db_path, position, bars, LIVE_STAMP_MS + 60_000, 99.0)
    assert first["reason"] is None
    assert repeated["reason"] is None
    assert repeated["evidence"]["consecutive_opposite_candles"] == 1
    assert repeated["state_revision"] == first["state_revision"]

    restarted_position = _position(
        "LONG", 95.0, (LIVE_STAMP_MS - 300_000) / 1000,
    )
    restarted = _evaluate(
        db_path, restarted_position, bars, LIVE_STAMP_MS + 60_000, 99.0,
    )
    assert restarted["position_uid"] == initialized["position_uid"]
    assert restarted["evidence"]["consecutive_opposite_candles"] == 1


@pytest.mark.parametrize(
    ("side", "first", "second"),
    [
        (
            "LONG",
            _bar(LIVE_STAMP_MS, 100.2, 100.3, 99.8, 100.0, ma5=99.0),
            _bar(LIVE_STAMP_MS + 60_000, 100.1, 100.2, 99.7, 99.9, ma5=99.0),
        ),
        (
            "SHORT",
            _bar(LIVE_STAMP_MS, 99.8, 100.2, 99.7, 100.0, ma5=101.0),
            _bar(LIVE_STAMP_MS + 60_000, 99.9, 100.3, 99.8, 100.1, ma5=101.0),
        ),
    ],
)
def test_two_reverse_candles_without_structure_or_ma_break_are_held(
    tmp_path, side, first, second,
):
    db_path = tmp_path / "protection.sqlite3"
    entry, quote = (95.0, 100.0) if side == "LONG" else (105.0, 100.0)
    position = _position(
        side, entry, (LIVE_STAMP_MS - 300_000) / 1000,
    )
    _evaluate(db_path, position, BASE_BARS, LIVE_STAMP_MS, quote)
    _evaluate(
        db_path, position, [*BASE_BARS, first],
        LIVE_STAMP_MS + 60_000, quote,
    )
    result = _evaluate(
        db_path, position, [*BASE_BARS, first, second],
        LIVE_STAMP_MS + 120_000, quote,
    )
    assert result["reason"] is None
    assert result["evidence"]["consecutive_opposite_candles"] == 2

def test_unclosed_reversal_does_not_count(tmp_path):
    db_path = tmp_path / "protection.sqlite3"
    position = _position("LONG", 95.0, (LIVE_STAMP_MS - 300_000) / 1000)
    result = _evaluate(db_path, position, BASE_BARS, LIVE_STAMP_MS, 99.0)
    assert result["status"] == "READY"
    assert result["reason"] is None
    assert result["evidence"]["consecutive_opposite_candles"] == 0


def test_position_uid_mismatch_blocks_protection(tmp_path):
    db_path = tmp_path / "protection.sqlite3"
    position = _position(
        "LONG", 95.0, (LIVE_STAMP_MS - 300_000) / 1000,
    )
    _evaluate(db_path, position, BASE_BARS, LIVE_STAMP_MS, 99.0)
    position["position_uid"] = "different-position"

    result = _evaluate(db_path, position, BASE_BARS, LIVE_STAMP_MS, 99.0)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "POSITION_PROTECTION_UID_MISMATCH"


def test_profit_lock_arms_at_eight_percent_and_exits_at_rising_floor(tmp_path):
    db_path = tmp_path / "protection.sqlite3"
    position = _position("LONG", 100.0, (LIVE_STAMP_MS - 300_000) / 1000)
    initial = _evaluate(db_path, position, BASE_BARS, LIVE_STAMP_MS, 109.0)
    assert initial["status"] == "READY"
    assert initial["evidence"]["max_net_roi"] >= 8.0
    assert initial["evidence"]["current_stop_roi"] is not None
    assert initial["reason"] is None

    neutral = _bar(
        LIVE_STAMP_MS, 100.0, 100.2, 99.8, 100.0,
    )
    retraced = _evaluate(
        db_path,
        position,
        [*BASE_BARS, neutral],
        LIVE_STAMP_MS + 60_000,
        105.0,
    )
    assert retraced["reason"] == "PRIORITY_EXIT_P1_NET_ROE_PROFIT_LOCK"


def test_profit_protection_precedes_the_midline_hold():
    position = _position("LONG", 95.0, (LIVE_STAMP_MS - 300_000) / 1000)
    result = evaluate_priority_exit(
        position,
        _frame(BASE_BARS, LIVE_STAMP_MS),
        None,
        100.0,
        {},
        profit_protection={
            "status": "READY",
            "reason": "PRIORITY_EXIT_P1_CONSECUTIVE_REVERSAL_PROFIT",
            "evidence": {"trigger": "TWO_CLOSED_REVERSAL_CANDLES"},
        },
        now=(LIVE_STAMP_MS + 20_000) / 1000,
    )
    assert result["priority"] == 1
    assert result["reason"] == "PRIORITY_EXIT_P1_CONSECUTIVE_REVERSAL_PROFIT"


def test_profit_lock_uses_existing_close_authority_and_position_uid(
    tmp_path, monkeypatch,
):
    db_path = tmp_path / "protection.sqlite3"
    monkeypatch.setattr(position_profit_protection, "DEFAULT_STATE_DB_PATH", db_path)
    position = _position(
        "LONG", 100.0, (LIVE_STAMP_MS - 300_000) / 1000,
    )
    position.update({"margin": 5.0, "leverage": 20.0})
    account = type("Account", (), {
        "positions": {"LOBSTER/USDT": position},
        "position_meta": {"LOBSTER/USDT": {}},
        "save_state": lambda self, **_kwargs: None,
    })()
    closed = []

    async def close_position(*args, **kwargs):
        closed.append((args, kwargs))
        return True

    initial = _evaluate(
        db_path, position, BASE_BARS, LIVE_STAMP_MS, 100.6,
    )
    assert initial["evidence"]["max_net_roi"] >= 8.0
    neutral = _bar(LIVE_STAMP_MS, 100.0, 100.2, 99.8, 100.0)
    frame = _frame([*BASE_BARS, neutral], LIVE_STAMP_MS + 60_000)
    now = (LIVE_STAMP_MS + 80_000) / 1000

    result = asyncio.run(evaluate_and_execute_exit(
        account,
        "LOBSTER/USDT",
        position,
        frame,
        None,
        100.35,
        close_position,
        now=now,
    ))

    assert result["status"] == "EXIT_EXECUTED"
    assert result["reason"] == "PRIORITY_EXIT_P1_NET_ROE_PROFIT_LOCK"
    assert len(closed) == 1
    authorization = account.position_meta["LOBSTER/USDT"]["priority_exit_authorization"]
    assert authorization["position_uid"] == position["position_uid"]
    assert _valid_authorization(
        position,
        account.position_meta["LOBSTER/USDT"],
        result["reason"],
        100.35,
        now=now,
    ) is not None


def test_position_uid_save_failure_retries_without_mutating_identity(
    tmp_path, monkeypatch,
):
    db_path = tmp_path / "protection.sqlite3"
    monkeypatch.setattr(position_profit_protection, "DEFAULT_STATE_DB_PATH", db_path)
    position = _position("LONG", 100.0, (LIVE_STAMP_MS - 300_000) / 1000)
    calls = []

    def save_state(self, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("temporary persistence failure")

    account = type("Account", (), {
        "positions": {"LOBSTER/USDT": position},
        "position_meta": {"LOBSTER/USDT": {}},
        "save_state": save_state,
    })()

    async def close_position(*_args, **_kwargs):
        raise AssertionError("blocked protection state must not close")

    frame = _frame(BASE_BARS, LIVE_STAMP_MS)
    now = (LIVE_STAMP_MS + 20_000) / 1000
    first = asyncio.run(evaluate_and_execute_exit(
        account, "LOBSTER/USDT", position, frame, None, 100.1,
        close_position, now=now,
    ))
    assert first["status"] != "EXIT_EXECUTED"
    assert "position_uid" not in position

    second = asyncio.run(evaluate_and_execute_exit(
        account, "LOBSTER/USDT", position, frame, None, 100.1,
        close_position, now=now,
    ))
    assert second["status"] != "EXIT_EXECUTED"
    assert position["position_uid"]
    assert len(calls) == 2
