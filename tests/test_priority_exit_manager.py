import asyncio
import math
import time

import pandas as pd
import pytest

from core.services.exits.priority_exit_manager import (
    CIRCUIT_BREAKER_COOLDOWN_SEC,
    FLAT_EXIT_BUFFER_ATR,
    HARD_SL_ATR_MULT,
    TRAILING_DIST_ATR,
    TRAILING_START_ATR,
    _valid_authorization,
    entry_circuit_breaker_cooldown_problem,
    evaluate_and_execute_exit,
    evaluate_priority_exit,
)


def _ltf(now, *, current_open=100.0, quote=100.0, previous_open=100.0,
         atr=1.0, closed_close=99.5, closed_mid=100.0, closed_atr=1.0,
         closed_middles=None):
    now_ms = now * 1000.0
    live_stamp = math.floor(now_ms / 60_000) * 60_000
    if closed_middles is None:
        closed_middles = (
            closed_mid - 0.2 * closed_atr,
            closed_mid - 0.1 * closed_atr,
            closed_mid,
        )
    closed_bars = [
        {
            "timestamp": live_stamp - (3 - index) * 60_000,
            "is_closed": True,
            "open": previous_open,
            "high": max(previous_open, closed_close) + 0.5,
            "low": min(previous_open, closed_close) - 0.5,
            "close": closed_close if index == 2 else middle,
            "kc_middle": middle,
            "atr": closed_atr,
        }
        for index, middle in enumerate(closed_middles)
    ]
    frame = pd.DataFrame(closed_bars + [{
            "timestamp": live_stamp,
            "is_closed": False,
            "open": current_open,
            "high": max(current_open, quote) + 0.1,
            "low": min(current_open, quote) - 0.1,
            "close": quote,
            "kc_middle": 100.0,
            "atr": atr,
        }])
    frame.attrs.update(
        timeframe_ms=60_000.0,
        snapshot_ms=now_ms,
    )
    return frame


def _htf(now, *, slope=0.0, lower=90.0, upper=110.0):
    now_ms = now * 1000.0
    live_stamp = math.floor(now_ms / 900_000) * 900_000
    middle = 100.0 + slope
    frame = pd.DataFrame([
        {
            "timestamp": live_stamp - 900_000,
            "is_closed": True,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "kc_middle": 100.0,
            "kc_upper": upper,
            "kc_lower": lower,
            "atr": 1.0,
        },
        {
            "timestamp": live_stamp,
            "is_closed": False,
            "open": middle,
            "high": middle + 1.0,
            "low": middle - 1.0,
            "close": middle,
            "kc_middle": middle,
            "kc_upper": upper,
            "kc_lower": lower,
            "atr": 1.0,
        },
    ])
    frame.attrs.update(
        timeframe_ms=900_000.0,
        snapshot_ms=now_ms,
    )
    return frame


def _position(side="LONG", *, entry=100.0, atr=1.0, opened=1_700_000_000.0):
    return {
        "side": side,
        "entry_price": entry,
        "entry_atr": atr,
        "open_timestamp": opened,
    }


@pytest.mark.parametrize(
    ("side", "live_open", "prior_open", "quote", "expected_trigger"),
    [
        ("LONG", 100.0, 100.0, 98.5, "SINGLE_CANDLE_ATR"),
        ("SHORT", 100.0, 100.0, 101.5, "SINGLE_CANDLE_ATR"),
        ("LONG", 100.0, 100.0, 99.1, "SINGLE_CANDLE_PERCENT"),
        ("SHORT", 100.0, 100.0, 100.9, "SINGLE_CANDLE_PERCENT"),
        ("LONG", 100.0, 103.0, 100.0, "TWO_CANDLE_DISPLACEMENT"),
        ("SHORT", 100.0, 97.0, 100.0, "TWO_CANDLE_DISPLACEMENT"),
    ],
)
def test_priority_zero_abnormal_rules_are_symmetric(
    side, live_open, prior_open, quote, expected_trigger,
):
    now = time.time()
    result = evaluate_priority_exit(
        _position(side),
        _ltf(
            now, current_open=live_open, previous_open=prior_open, quote=quote,
        ),
        None,
        quote,
        {},
        now=now,
    )

    assert result["priority"] == 0
    assert result["cooldown"] is True
    assert result["evidence"]["trigger"] == expected_trigger


def test_priority_zero_precedes_higher_timeframe_reversal():
    now = time.time()
    result = evaluate_priority_exit(
        _position("LONG"),
        _ltf(now, quote=98.5),
        _htf(now, slope=-0.1),
        98.5,
        {},
        now=now,
    )

    assert result["priority"] == 0


@pytest.mark.parametrize(
    ("side", "slope", "closed_middles"),
    [
        ("LONG", -0.1, (100.2, 100.1, 100.0)),
        ("SHORT", 0.1, (99.8, 99.9, 100.0)),
    ],
)
def test_priority_one_closes_on_confirmed_ltf_and_macro_reversal(
    side, slope, closed_middles,
):
    now = time.time()
    result = evaluate_priority_exit(
        _position(side),
        _ltf(now, closed_middles=closed_middles),
        _htf(now, slope=slope),
        100.0,
        {},
        now=now,
    )

    assert result["priority"] == 1
    assert result["reason"] == "PRIORITY_EXIT_P1_MTF_KC_REVERSAL"


@pytest.mark.parametrize(
    ("side", "slope", "closed_middles"),
    [
        ("LONG", -0.1, (99.8, 99.9, 100.0)),
        ("SHORT", 0.1, (100.2, 100.1, 100.0)),
    ],
)
def test_priority_one_holds_until_ltf_midline_confirms_macro_reversal(
    side, slope, closed_middles,
):
    now = time.time()
    result = evaluate_priority_exit(
        _position(side),
        _ltf(now, closed_middles=closed_middles),
        _htf(now, slope=slope),
        100.0,
        {},
        now=now,
    )

    assert result["priority"] is None
    assert result["hold_reason"] == "VETO_MTF_LTF_MIDLINE_DIRECTION_UNCONFIRMED"


def test_priority_two_hard_stop_remains_active_when_ltf_reversal_is_unconfirmed():
    now = time.time()
    result = evaluate_priority_exit(
        _position("LONG", entry=100.0, atr=1.0),
        None,
        _htf(now, slope=-0.1),
        98.0,
        {},
        now=now,
    )

    assert result["priority"] == 2
    assert result["evidence"]["trigger"] == "ENTRY_ATR_HARD_STOP"


def test_priority_two_applies_entry_atr_stop_and_macro_outer_band():
    now = time.time()
    stop_result = evaluate_priority_exit(
        _position("LONG", entry=100.0, atr=1.0),
        None,
        None,
        100.0 - HARD_SL_ATR_MULT,
        {},
        now=now,
    )
    assert stop_result["priority"] == 2
    assert stop_result["evidence"]["trigger"] == "ENTRY_ATR_HARD_STOP"

    band_result = evaluate_priority_exit(
        _position("LONG", entry=100.0, atr=10.0),
        None,
        _htf(now, lower=90.0),
        89.0,
        {},
        now=now,
    )
    assert band_result["priority"] == 2
    assert band_result["evidence"]["trigger"] == "OPPOSITE_HTF_KC_BAND"


def test_priority_three_does_not_close_on_atr_pullback():
    now = time.time()
    metadata = {"priority_exit_state": {"favorable_extreme": 1004.0}}
    result = evaluate_priority_exit(
        _position("LONG", entry=1000.0),
        _ltf(
            now, current_open=1003.5, previous_open=1003.5,
            quote=1001.5, atr=2.0,
        ),
        None,
        1001.5,
        metadata,
        now=now,
    )

    assert result["priority"] is None
    assert result["hold_reason"] == "VETO_EXIT_BY_PULLBACK"


def test_priority_three_does_not_close_on_pullback_when_trend_is_aligned():
    now = time.time()
    result = evaluate_priority_exit(
        _position("LONG", entry=1000.0),
        _ltf(
            now, current_open=1003.5, previous_open=1003.5,
            quote=1001.5, atr=2.0, closed_middles=(99.8, 99.9, 100.0),
        ),
        _htf(now, slope=0.1),
        1001.5,
        {"priority_exit_state": {"favorable_extreme": 1004.0}},
        now=now,
    )

    assert result["priority"] is None
    assert result["hold_reason"] == "VETO_EXIT_BY_PULLBACK"


def test_priority_four_requires_flat_transition_and_closed_ltf_break():
    now = time.time()
    metadata = {}
    evaluate_priority_exit(
        _position("LONG"),
        _ltf(now, quote=100.0),
        _htf(now, slope=0.1),
        100.0,
        metadata,
        now=now,
    )

    result = evaluate_priority_exit(
        _position("LONG"),
        _ltf(
            now, quote=100.0, closed_close=99.5, closed_mid=100.0,
            closed_atr=1.0,
        ),
        _htf(now, slope=0.0),
        100.0,
        metadata,
        now=now,
    )
    assert result["priority"] == 4
    assert result["evidence"]["threshold"] == pytest.approx(
        100.0 - FLAT_EXIT_BUFFER_ATR
    )


def test_p0_execution_persists_cooldown_and_short_lived_close_authority():
    now = time.time()
    account = type("Account", (), {
        "positions": {"X/USDT": _position("LONG")},
        "position_meta": {"X/USDT": {}},
        "entry_filter_last": {},
        "save_state": lambda self, **_kwargs: None,
    })()
    closed = []

    async def close_position(*args, **kwargs):
        closed.append((args, kwargs))
        return False

    result = asyncio.run(evaluate_and_execute_exit(
        account,
        "X/USDT",
        account.positions["X/USDT"],
        _ltf(now, quote=98.5),
        None,
        98.5,
        close_position,
        now=now,
    ))

    assert result["priority"] == 0
    assert result["success"] is False
    assert entry_circuit_breaker_cooldown_problem(
        account, "X/USDT", now=now + CIRCUIT_BREAKER_COOLDOWN_SEC - 1,
    ) == "BLOCKED_CIRCUIT_BREAKER_COOLDOWN"
    auth = account.position_meta["X/USDT"]["priority_exit_authorization"]
    assert _valid_authorization(
        account.positions["X/USDT"], account.position_meta["X/USDT"],
        auth["reason"], 98.5, now=now,
    ) is not None
    assert _valid_authorization(
        account.positions["X/USDT"], account.position_meta["X/USDT"],
        auth["reason"], 99.0, now=now,
    ) is None
    assert len(closed) == 1


@pytest.mark.parametrize(
    "cooldown_state",
    [
        {"priority_exit_cooldown_until": {"X/USDT": "invalid"}},
        {"priority_exit_cooldown_until": []},
    ],
)
def test_invalid_persisted_cooldown_state_fails_closed(cooldown_state):
    account = type("Account", (), {"entry_filter_last": cooldown_state})()

    assert entry_circuit_breaker_cooldown_problem(
        account, "X/USDT",
    ) == "BLOCKED_CIRCUIT_BREAKER_COOLDOWN_STATE_INVALID"
