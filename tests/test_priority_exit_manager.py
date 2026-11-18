import asyncio
import math
import time

import pandas as pd
import pytest

from core.services.exits.priority_exit_manager import (
    CIRCUIT_BREAKER_COOLDOWN_SEC,
    _valid_authorization,
    entry_circuit_breaker_cooldown_problem,
    evaluate_and_execute_exit,
    evaluate_priority_exit,
)


def _ltf(now, *, current_open=100.0, quote=100.0, previous_open=100.0,
         atr=1.0, closed_close=99.5, closed_mid=100.0, closed_atr=1.0,
         closed_middles=None, live_mid=100.0, closed_lower=99.0,
         closed_upper=101.0, closed_closes=None, closed_ma15=None):
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
            "close": (
                closed_closes[index]
                if closed_closes is not None
                else closed_close if index == 2 else middle
            ),
            "kc_middle": middle,
            "kc_lower": closed_lower,
            "kc_upper": closed_upper,
            "ma15": (
                closed_ma15[index] if closed_ma15 is not None else middle
            ),
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
            "kc_middle": live_mid,
            "kc_lower": live_mid - 1.0,
            "kc_upper": live_mid + 1.0,
            "ma15": live_mid,
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
        ("LONG", 98.3, "SINGLE_WATERFALL_1_6_ATR_KC_CROSS"),
        ("SHORT", 101.7, "SINGLE_WATERFALL_1_6_ATR_KC_CROSS"),
        ("LONG", 99.1, "SINGLE_CANDLE_PERCENT"),
        ("SHORT", 100.9, "SINGLE_CANDLE_PERCENT"),
    ],
)
def test_priority_zero_closes_immediately_on_any_configured_threshold(
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

    assert result["priority"] == 0
    assert result["cooldown"] is True
    assert result["evidence"]["trigger"] == expected_trigger


def test_priority_zero_precedes_higher_timeframe_reversal():
    now = time.time()
    position = _position("LONG")
    metadata = {}
    evaluate_priority_exit(
        position,
        _ltf(now, quote=98.3),
        None,
        98.3,
        metadata,
        now=now,
    )
    live_bar_ms = math.floor(now * 1000.0 / 60_000) * 60_000
    next_now = (live_bar_ms + 61_000) / 1000.0
    result = evaluate_priority_exit(
        position,
        _ltf(next_now, quote=98.3),
        _htf(now, slope=-0.1),
        98.3,
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
        position, _ltf(now, quote=98.3 if side == "LONG" else 101.7),
        None, 98.3 if side == "LONG" else 101.7, metadata, now=now,
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
def test_priority_one_closes_on_htf_reversal_without_ltf_confirmation(
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
    assert result["reason"] == "PRIORITY_EXIT_P1_HTF_KC_REVERSAL"


@pytest.mark.parametrize(
    ("side", "slope", "closed_middles"),
    [
        ("LONG", -0.01, (99.8, 99.9, 100.0)),
        ("SHORT", 0.01, (100.2, 100.1, 100.0)),
    ],
)
def test_priority_one_ignores_htf_slopes_inside_flat_threshold(
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
    assert result["reason"] is None


def test_per_position_atr_hard_stop_does_not_close_without_market_emergency():
    now = time.time()
    result = evaluate_priority_exit(
        _position("LONG", entry=100.0, atr=1.0),
        None,
        None,
        97.0,
        {},
        now=now,
    )

    assert result["priority"] is None
    assert result["reason"] is None


@pytest.mark.parametrize(
    ("side", "first_open", "second_open", "second_close"),
    [
        ("LONG", 101.8, 99.9, 98.2),
        ("SHORT", 98.2, 100.1, 101.8),
    ],
)
def test_priority_zero_requires_two_consecutive_abnormal_candles(
    side, first_open, second_open, second_close,
):
    now = time.time()
    position = _position(side)
    metadata = {}
    first_quote = 100.1 if side == "LONG" else 99.9
    first_result = evaluate_priority_exit(
        position,
        _ltf(now, current_open=first_open, quote=first_quote),
        None,
        first_quote,
        metadata,
        now=now,
    )
    assert first_result["priority"] is None
    assert metadata["priority_exit_state"]["abnormal_exit_pending"]

    first_bar_ms = math.floor(now * 1000.0 / 60_000) * 60_000
    next_now = (first_bar_ms + 61_000) / 1000.0
    result = evaluate_priority_exit(
        position,
        _ltf(
            next_now,
            current_open=second_open,
            quote=second_close,
            previous_open=second_open,
            closed_close=second_close,
        ),
        None,
        second_close,
        metadata,
        now=next_now,
    )

    assert result["priority"] == 0
    assert result["evidence"]["trigger"] == "CONSECUTIVE_WATERFALL_2_3_ATR_KC_CROSS"


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


@pytest.mark.parametrize(
    ("side", "current_open", "quote", "middles", "closed_close", "outer"),
    [
        ("LONG", 99.0, 98.8, (100.3, 100.2, 100.1), 98.9, 99.0),
        ("SHORT", 101.0, 101.2, (99.7, 99.8, 99.9), 101.1, 101.0),
    ],
)
def test_priority_two_requires_midline_turn_and_closed_outer_band_break(
    side, current_open, quote, middles, closed_close, outer,
):
    now = time.time()
    result = evaluate_priority_exit(
        _position(side),
        _ltf(
            now,
            current_open=current_open,
            quote=quote,
            closed_middles=middles,
            closed_close=closed_close,
            live_mid=100.0,
            closed_lower=outer if side == "LONG" else 99.0,
            closed_upper=outer if side == "SHORT" else 101.0,
        ),
        None,
        quote,
        {},
        now=now,
    )

    assert result["priority"] == 2
    assert result["reason"] == "PRIORITY_EXIT_P2_KC_MIDLINE_REVERSAL"
    assert result["evidence"]["structure_confirmation"] == (
        "CLOSED_CLOSE_BEYOND_OPPOSITE_OUTER_BAND"
    )


@pytest.mark.parametrize(
    ("side", "middles", "closes", "ma15"),
    [
        ("LONG", (100.3, 100.2, 100.1), (100.4, 100.0, 99.9), (100.4, 100.3, 100.2)),
        ("SHORT", (99.7, 99.8, 99.9), (99.6, 100.0, 100.1), (99.6, 99.7, 99.8)),
    ],
)
def test_priority_two_allows_two_close_and_ma15_structure_confirmation(
    side, middles, closes, ma15,
):
    now = time.time()
    result = evaluate_priority_exit(
        _position(side),
        _ltf(
            now,
            current_open=100.0,
            quote=100.1 if side == "LONG" else 99.9,
            closed_middles=middles,
            closed_closes=closes,
            closed_ma15=ma15,
            live_mid=100.0,
        ),
        None,
        100.1 if side == "LONG" else 99.9,
        {},
        now=now,
    )

    assert result["priority"] == 2
    assert result["evidence"]["structure_confirmation"] == (
        "TWO_CLOSES_BEYOND_MIDLINE_WITH_MA15_TURN"
    )


def test_flat_htf_does_not_exit_before_ltf_structure_reversal():
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
    assert result["hold_reason"].startswith("LONG_HOLD_")


def test_true_pivot_does_not_override_the_kc_exit_policy():
    now = time.time()
    result = evaluate_priority_exit(
        _position("LONG"),
        _ltf(now, quote=100.0),
        None,
        100.0,
        {},
        now=now,
    )

    assert result["priority"] is None
    assert result["reason"] is None


@pytest.mark.parametrize(
    ("side", "closed_middles"),
    [
        ("LONG", (100.3, 100.2, 100.1)),
        ("SHORT", (99.7, 99.8, 99.9)),
    ],
)
def test_consolidation_blocks_kc_exit_after_true_pivot_check(
    side, closed_middles, monkeypatch,
):
    from core.services import market_consolidation_filter
    from core.services.exits import priority_exit_manager

    monkeypatch.setattr(
        market_consolidation_filter,
        "evaluate_market_blacklist",
        lambda *_args, **_kwargs: (
            True, "VETO_MA5_MA15_CHOP", {"crossings": 2},
        ),
    )
    monkeypatch.setattr(
        priority_exit_manager,
        "_confirmed_true_pivot",
        lambda *_args, **_kwargs: (False, {}),
    )
    monkeypatch.setattr(
        priority_exit_manager,
        "_confirmed_kc_midline_reversal",
        lambda *_args, **_kwargs: {"trigger": "KC_STRUCTURE_CONFIRMED"},
    )
    now = time.time()
    result = evaluate_priority_exit(
        _position(side),
        _ltf(now, closed_middles=closed_middles),
        None,
        100.0,
        {},
        now=now,
    )

    assert result["priority"] is None
    assert result["hold_reason"] == f"{side}_HOLD_LTF_CONSOLIDATION"
    assert result["evidence"]["reason"] == "VETO_MA5_MA15_CHOP"


def test_confirmed_true_pivot_remains_exit_authority_during_consolidation(
    monkeypatch,
):
    from core.services import market_consolidation_filter
    from core.services.exits import priority_exit_manager

    monkeypatch.setattr(
        market_consolidation_filter,
        "evaluate_market_blacklist",
        lambda *_args, **_kwargs: (
            True, "VETO_MA5_MA15_CHOP", {"crossings": 2},
        ),
    )
    monkeypatch.setattr(
        priority_exit_manager,
        "_confirmed_true_pivot",
        lambda *_args, **_kwargs: (True, {"true_pivot_type": "CONFIRMED"}),
    )
    now = time.time()
    result = evaluate_priority_exit(
        _position("LONG"),
        _ltf(now, closed_middles=(100.3, 100.2, 100.1)),
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
    assert first_result["priority"] == 0
    assert first_result["success"] is False

    assert first_result["priority"] == 0
    assert first_result["success"] is False
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
    ("side", "quotes"),
    [
        ("LONG", (100.1, 100.2, 100.0, 99.9)),
        ("SHORT", (99.9, 99.8, 100.0, 100.1)),
    ],
)
def test_ma5_reversal_is_ignored_until_kc_structure_exit(side, quotes):
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
    assert results[-1]["hold_reason"].startswith(f"{side}_HOLD_")


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
