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
    ("side", "quote", "expected_trigger"),
    [
        ("LONG", 98.5, "SINGLE_CANDLE_ATR"),
        ("SHORT", 101.5, "SINGLE_CANDLE_ATR"),
        ("LONG", 99.1, "SINGLE_CANDLE_PERCENT"),
        ("SHORT", 100.9, "SINGLE_CANDLE_PERCENT"),
    ],
)
def test_priority_zero_waits_for_a_second_consecutive_abnormal_candle(
    side, quote, expected_trigger,
):
    now = time.time()
    position = _position(side)
    metadata = {}
    result = evaluate_priority_exit(
        position,
        _ltf(now, quote=quote),
        None,
        quote,
        metadata,
        now=now,
    )

    assert result["priority"] is None
    assert result["hold_reason"] == "WAITING_FOR_SECOND_CONSECUTIVE_ABNORMAL_CANDLE"

    live_bar_ms = math.floor(now * 1000.0 / 60_000) * 60_000
    next_now = (live_bar_ms + 61_000) / 1000.0
    result = evaluate_priority_exit(
        position,
        _ltf(next_now, quote=quote),
        None,
        quote,
        metadata,
        now=next_now,
    )

    assert result["priority"] == 0
    assert result["cooldown"] is True
    assert result["evidence"]["trigger"] == "CONSECUTIVE_ABNORMAL_CANDLES"
    assert (
        result["evidence"]["second_abnormal_candle"]["trigger"]
        == expected_trigger
    )
    third_bar_now = (live_bar_ms + 121_000) / 1000.0
    retry = evaluate_priority_exit(
        position,
        _ltf(third_bar_now, quote=100.2 if side == "LONG" else 99.8),
        None,
        100.2 if side == "LONG" else 99.8,
        metadata,
        now=third_bar_now,
    )
    assert retry["priority"] == 0
    assert retry["evidence"]["close_retry"] is True


def test_priority_zero_precedes_higher_timeframe_reversal():
    now = time.time()
    position = _position("LONG")
    metadata = {}
    evaluate_priority_exit(
        position,
        _ltf(now, quote=98.5),
        None,
        98.5,
        metadata,
        now=now,
    )
    live_bar_ms = math.floor(now * 1000.0 / 60_000) * 60_000
    next_now = (live_bar_ms + 61_000) / 1000.0
    result = evaluate_priority_exit(
        position,
        _ltf(next_now, quote=98.5),
        _htf(now, slope=-0.1),
        98.5,
        metadata,
        now=next_now,
    )

    assert result["priority"] == 0


@pytest.mark.parametrize(
    ("side", "aligned_close"),
    [("LONG", 100.5), ("SHORT", 99.5)],
)
def test_priority_zero_clears_after_the_next_candle_closes_without_anomaly(
    side, aligned_close,
):
    now = time.time()
    position = _position(side)
    metadata = {}
    evaluate_priority_exit(
        position, _ltf(now, quote=98.5 if side == "LONG" else 101.5),
        None, 98.5 if side == "LONG" else 101.5, metadata, now=now,
    )
    first_bar_ms = math.floor(now * 1000.0 / 60_000) * 60_000
    third_bar_now = (first_bar_ms + 121_000) / 1000.0
    quote = 100.2 if side == "LONG" else 99.8
    result = evaluate_priority_exit(
        position,
        _ltf(
            third_bar_now,
            quote=quote,
            closed_close=aligned_close,
        ),
        None,
        quote,
        metadata,
        now=third_bar_now,
    )

    assert result["priority"] is None
    assert "abnormal_exit_pending" not in metadata["priority_exit_state"]


@pytest.mark.parametrize(
    ("side", "slope", "closed_middles"),
    [
        ("LONG", -0.1, (100.2, 100.1, 100.0)),
        ("SHORT", 0.1, (99.8, 99.9, 100.0)),
    ],
)
def test_priority_one_closes_on_confirmed_ltf_and_macro_reversal(
    side, slope, closed_middles, monkeypatch,
):
    from core.services.exits import strict_true_pivot

    pivot_reason = (
        "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT"
        if side == "LONG" else "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
    )
    monkeypatch.setattr(
        strict_true_pivot,
        "profitable_true_pivot_exit",
        lambda *_args, **_kwargs: (pivot_reason, {"exit_policy": "verified"}),
    )
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
    assert result["reason"] == "PRIORITY_EXIT_P1_TRUE_PEAK_VALLEY"


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
    assert result["reason"] is None


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
    assert result["reason"] is None


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
    assert result["priority"] is None
    assert result["hold_reason"] == "VETO_FLAT_EXIT_UNTIL_TRUE_PEAK"


def test_true_pivot_closes_without_mtf_or_flat_condition(monkeypatch):
    from core.services.exits import strict_true_pivot

    for side, pivot_reason in (
        ("LONG", "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT"),
        ("SHORT", "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"),
    ):
        monkeypatch.setattr(
            strict_true_pivot,
            "profitable_true_pivot_exit",
            lambda *_args, result=pivot_reason, **_kwargs: (
                result, {"exit_policy": "verified"},
            ),
        )
        now = time.time()
        result = evaluate_priority_exit(
            _position(side),
            _ltf(now, quote=100.0),
            None,
            100.0,
            {},
            now=now,
        )
        assert result["priority"] == 1
        assert result["reason"] == "PRIORITY_EXIT_P1_TRUE_PEAK_VALLEY"


@pytest.mark.parametrize(
    ("side", "pivot_type"),
    [("LONG", "TRUE_PEAK_CONFIRMED"), ("SHORT", "TRUE_VALLEY_CONFIRMED")],
)
def test_raw_three_point_pivot_without_profit_confirmation_does_not_close(
    side, pivot_type, monkeypatch,
):
    from core.gates import holding_protection_gate
    from core.services.exits import strict_true_pivot

    monkeypatch.setattr(
        holding_protection_gate, "has_confirmed_true_pivot",
        lambda *_args: (True, pivot_type, {"raw_pivot": True}),
    )
    monkeypatch.setattr(
        strict_true_pivot, "profitable_true_pivot_exit",
        lambda *_args, **_kwargs: None,
    )
    now = time.time()
    result = evaluate_priority_exit(
        _position(side), _ltf(now), None, 100.0, {}, now=now,
    )

    assert result["priority"] is None
    assert result["reason"] is None


def test_p0_execution_persists_cooldown_and_short_lived_close_authority():
    now = time.time()
    live_bar_ms = math.floor(now * 1000.0 / 60_000) * 60_000
    next_now = (live_bar_ms + 61_000) / 1000.0
    position = _position("LONG")
    account = type("Account", (), {
        "positions": {"X/USDT": position},
        "position_meta": {"X/USDT": {}},
        "entry_filter_last": {},
        "save_state": lambda self, **_kwargs: None,
    })()
    closed = []

    async def close_position(*args, **kwargs):
        closed.append((args, kwargs))
        return False

    first_result = asyncio.run(evaluate_and_execute_exit(
        account,
        "X/USDT",
        position,
        _ltf(now, quote=98.5),
        None,
        98.5,
        close_position,
        now=now,
    ))
    assert first_result["status"] == "HOLD"
    assert not closed

    result = asyncio.run(evaluate_and_execute_exit(
        account,
        "X/USDT",
        position,
        _ltf(next_now, quote=98.5),
        None,
        98.5,
        close_position,
        now=next_now,
    ))

    assert result["priority"] == 0
    assert result["success"] is False
    assert entry_circuit_breaker_cooldown_problem(
        account, "X/USDT", now=next_now + CIRCUIT_BREAKER_COOLDOWN_SEC - 1,
    ) == "BLOCKED_CIRCUIT_BREAKER_COOLDOWN"
    auth = account.position_meta["X/USDT"]["priority_exit_authorization"]
    assert _valid_authorization(
        account.positions["X/USDT"], account.position_meta["X/USDT"],
        auth["reason"], 98.5, now=next_now,
    ) is not None
    assert _valid_authorization(
        account.positions["X/USDT"], account.position_meta["X/USDT"],
        auth["reason"], 99.0, now=next_now,
    ) is None
    assert len(closed) == 1


@pytest.mark.parametrize(
    ("side", "quotes"),
    [
        ("LONG", (100.1, 100.2, 100.0, 99.9)),
        ("SHORT", (99.9, 99.8, 100.0, 100.1)),
    ],
)
def test_ma5_reversal_never_closes_without_strict_true_pivot(side, quotes, monkeypatch):
    from core.services.exits import strict_true_pivot

    monkeypatch.setattr(
        strict_true_pivot,
        "profitable_true_pivot_exit",
        lambda *_args, **_kwargs: None,
    )
    now = time.time()
    position = _position(side)
    metadata = {}

    results = []
    for quote in quotes:
        live_frame = _ltf(
            now, current_open=100.0, quote=quote, closed_close=100.0,
        )
        earlier_closed = live_frame.iloc[0].copy()
        earlier_closed["timestamp"] = float(earlier_closed["timestamp"]) - 60_000
        live_frame = pd.concat(
            [earlier_closed.to_frame().T, live_frame], ignore_index=True,
        )
        live_frame.attrs.update(
            timeframe_ms=60_000.0, snapshot_ms=now * 1000.0,
        )
        results.append(evaluate_priority_exit(
            position, live_frame, None, quote, metadata, now=now,
        ))

    assert all(result["priority"] is None for result in results)
    assert results[-1]["hold_reason"] == (
        "VETO_MA5_TURN_UNTIL_TRUE_PEAK"
        if side == "LONG" else "VETO_MA5_TURN_UNTIL_TRUE_VALLEY"
    )


def test_ma5_priority_exit_cannot_trigger_immediate_reverse_entry(monkeypatch):
    from core.services.execution_manager import ExecutionManager

    account = type("Account", (), {
        "positions": {},
        "daily_loss_limit_hit": lambda self: (False, 0.0),
    })()
    engine = type("Engine", (), {})()
    opened = []

    async def execute(*args, **kwargs):
        opened.append((args, kwargs))
        account.positions["X/USDT"] = {"side": "SHORT"}
        return True

    engine._execute_confirmed_channel_break = execute
    result = asyncio.run(ExecutionManager._attempt_priority_exit_reversal(
        symbol="X/USDT",
        account=account,
        engine_or_account=engine,
        frame_1m=pd.DataFrame(),
        quote=99.0,
        position=_position("LONG"),
        exit_result={
            "priority": 3,
            "reason": "PRIORITY_EXIT_P3_MA5_DIRECTION_REVERSAL",
        },
    ))

    assert result == {"status": "NOT_APPLICABLE"}
    assert not opened


def test_priority_exit_reversal_fails_closed_when_not_flat():
    from core.services.execution_manager import ExecutionManager

    account = type("Account", (), {
        "positions": {"X/USDT": _position("LONG")},
    })()
    result = asyncio.run(ExecutionManager._attempt_priority_exit_reversal(
        symbol="X/USDT",
        account=account,
        engine_or_account=object(),
        frame_1m=pd.DataFrame(),
        quote=99.0,
        position=_position("LONG"),
        exit_result={
            "priority": 2,
            "reason": "PRIORITY_EXIT_P2_ENTRY_HARD_STOP",
        },
    ))

    assert result["reason"] == "REVERSAL_REQUIRES_CONFIRMED_FLAT"


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
