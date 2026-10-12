"""Isolated regression tests for the three high-volatility entry gates."""
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from core.gates.pipeline import EntryGatePipeline, strict_kc_entry_problem
from core.services import entry_contract


def _momentum_burst_frame(side: str) -> tuple[pd.DataFrame, float]:
    rows = []
    is_long = side == "LONG"

    for index in range(10):
        rows.append({
            "timestamp": (index + 1) * 60_000,
            "is_closed": True,
            "open": 100.0,
            "high": 100.3,
            "low": 99.7,
            "close": 100.0,
            "atr": 1.0,
            "kc_lower": 96.0,
            "kc_middle": 100.0,
            "kc_upper": 104.0,
            "ma5": 100.3 if is_long else 99.7,
            "ma15": 100.5 if is_long else 99.5,
        })

    quote = 101.0 if is_long else 99.0
    rows.append({
        "timestamp": 660_000,
        "is_closed": False,
        "open": 100.0,
        "high": quote + 1.0 if is_long else 100.0,
        "low": 100.0 if is_long else quote - 1.0,
        "close": quote,
        "atr": 1.0,
        "kc_lower": 96.0,
        "kc_middle": 101.05 if is_long else 98.95,
        "kc_upper": 104.0,
        "ma5": 100.6 if is_long else 99.4,
        "ma15": 100.5 if is_long else 99.5,
    })

    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame, quote


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_momentum_burst_accepts_fifty_percent_body_and_one_x_directional_wick(
    side: str,
) -> None:
    frame, quote = _momentum_burst_frame(side)

    decision = EntryGatePipeline.detect_momentum_burst(frame, quote, side=side)

    assert decision is not None
    assert decision["body_ratio"] == pytest.approx(0.50)
    assert decision["body_atr"] == pytest.approx(1.0)


def _kc_space_frame(
    side: str,
    *,
    body_atr: float,
    remaining_space_ratio: float,
) -> tuple[pd.DataFrame, float]:
    lower, middle, upper = 96.0, 100.0, 104.0
    quote = (
        upper - remaining_space_ratio * (upper - lower)
        if side == "LONG"
        else lower + remaining_space_ratio * (upper - lower)
    )
    opening = quote - body_atr if side == "LONG" else quote + body_atr
    frame = pd.DataFrame([{
        "timestamp": 60_000,
        "is_closed": False,
        "open": opening,
        "high": quote,
        "low": opening,
        "close": quote,
        "atr": 1.0,
        "kc_lower": lower,
        "kc_middle": middle,
        "kc_upper": upper,
    }])
    return frame, quote


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_strong_body_at_point_seven_atr_uses_fifteen_percent_kc_room(
    side: str,
) -> None:
    frame, quote = _kc_space_frame(
        side, body_atr=0.70, remaining_space_ratio=0.20,
    )

    assert strict_kc_entry_problem(frame, quote, side) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_body_below_point_seven_atr_keeps_twenty_five_percent_kc_room(
    side: str,
) -> None:
    frame, quote = _kc_space_frame(
        side, body_atr=0.69, remaining_space_ratio=0.20,
    )

    expected = (
        "INSUFFICIENT_UPPER_SPACE"
        if side == "LONG"
        else "INSUFFICIENT_LOWER_SPACE"
    )
    assert strict_kc_entry_problem(frame, quote, side) == expected


@pytest.mark.parametrize(
    ("exit_reason", "pnl", "elapsed_seconds", "expected_reason"),
    [
        ("TAKE_PROFIT", 1.0, 59.0, "BLOCKED_BY_COOLDOWN_SAME_SIDE"),
        ("TAKE_PROFIT", 1.0, 61.0, "BLOCKED_BY_MA5_SLOPE_INVALID"),
        ("STOP_LOSS", -1.0, 120.0, "BLOCKED_BY_COOLDOWN_SAME_SIDE"),
        ("STOP_LOSS", -1.0, 181.0, "BLOCKED_BY_MA5_SLOPE_INVALID"),
    ],
)
def test_same_side_cooldown_uses_exit_type_and_sixty_or_one_eighty_seconds(
    monkeypatch: pytest.MonkeyPatch,
    exit_reason: str,
    pnl: float,
    elapsed_seconds: float,
    expected_reason: str,
) -> None:
    monkeypatch.setattr(
        entry_contract, "same_bar_close_entry_problem",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        entry_contract, "opposite_side_close_cooldown_problem",
        lambda *_args: None,
    )
    account = SimpleNamespace(
        last_closed_at={"CAP/USDT": time.time() - elapsed_seconds},
        last_closed_side={"CAP/USDT": "LONG"},
        trades=[{
            "symbol": "CAP/USDT",
            "action": "CLOSE_LONG",
            "status": "CLOSED",
            "pnl": pnl,
            "reason": exit_reason,
        }],
    )

    passed, reason = entry_contract.pre_flight_safety_check(
        None, quote=100.0, side="LONG", account=account, symbol="CAP/USDT",
    )

    assert passed is False
    assert reason == expected_reason
