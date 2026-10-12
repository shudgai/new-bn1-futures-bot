from types import SimpleNamespace

import pandas as pd
import pytest

from core.services import entry_contract


def _frame(current_bar_ms):
    rows = []
    for index in range(11):
        stamp = current_bar_ms - (10 - index) * 60_000
        rows.append({
            "timestamp": stamp,
            "is_closed": index < 10,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "atr": 0.5,
        })
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


def _account(now_seconds, *, side="SHORT", close_price=100.0):
    current_bar_ms = int(now_seconds * 1000)
    return SimpleNamespace(
        last_closed_at={"LOBSTER/USDT": now_seconds - 120.0},
        last_closed_side={"LOBSTER/USDT": side},
        trades=[
            {
                "id": current_bar_ms - 1_000,
                "symbol": "LOBSTER/USDT",
                "action": f"CLOSE_{side}",
                "price": close_price,
                "status": "CLOSED",
            },
        ],
    )


def test_same_side_close_blocks_reentry_within_half_atr(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(entry_contract.time, "time", lambda: now)
    frame = _frame(int(now * 1000))
    account = _account(now)

    assert entry_contract.same_side_anti_churn_problem(
        account, "LOBSTER/USDT", frame, "SHORT", 100.25,
    ) == "BLOCKED_SAME_SIDE_CLOSE_PRICE_REENTRY"


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        (100.2500001, None),  # just outside the inclusive 0.5 ATR band
        (100.0, "BLOCKED_SAME_SIDE_CLOSE_PRICE_REENTRY"),
    ],
)
def test_same_side_close_half_atr_boundary(monkeypatch, quote, expected):
    now = 1_800_000_000.0
    monkeypatch.setattr(entry_contract.time, "time", lambda: now)
    frame = _frame(int(now * 1000))
    account = _account(now)

    assert entry_contract.same_side_anti_churn_problem(
        account, "LOBSTER/USDT", frame, "SHORT", quote,
    ) == expected


def test_opposite_side_close_does_not_start_same_side_deep_cooldown(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(entry_contract.time, "time", lambda: now)
    frame = _frame(int(now * 1000))
    account = _account(now, side="LONG")

    assert entry_contract.same_side_anti_churn_problem(
        account, "LOBSTER/USDT", frame, "SHORT", 100.2,
    ) is None


def test_invalid_atr_fails_closed_after_same_side_close(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(entry_contract.time, "time", lambda: now)
    frame = _frame(int(now * 1000))
    frame.loc[frame.index[-2], "atr"] = float("nan")
    account = _account(now)

    assert entry_contract.same_side_anti_churn_problem(
        account, "LOBSTER/USDT", frame, "SHORT", 100.2,
    ) == "BLOCKED_SAME_SIDE_CLOSE_INVALID_MARKET_DATA"
