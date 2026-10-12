import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.exits.confirmed_structure_exit import (
    EXIT_REASON,
    STATE_KEY,
    evaluate_confirmed_structure_exit,
)


def _snapshot(history, quote_ms):
    live_bar_ms = quote_ms // 60_000 * 60_000
    closed_bar_ms = history[-1]["ms"]
    return {
        "reason": None,
        "quote_ms": quote_ms,
        "live_bar_ms": live_bar_ms,
        "closed_bar_ms": closed_bar_ms,
        "snapshot_bar_id": closed_bar_ms,
        "history_5": history,
    }


def _bar(ms, low, high, close=None):
    return {
        "ms": ms,
        "o": (low + high) / 2,
        "h": high,
        "l": low,
        "c": (low + high) / 2 if close is None else close,
        "kc_middle": (low + high) / 2,
    }


def _position(side, opened_at=60.0):
    return {
        "side": side,
        "open_timestamp": opened_at,
        "entry_price": 100.0,
        "qty": 1.0,
    }


def test_long_structure_exit_requires_fresh_closed_close_below_confirmed_pivot():
    position = _position("LONG")
    meta = {}
    history = [
        _bar(60_000, 10, 14),
        _bar(120_000, 8, 13),
        _bar(180_000, 9, 12),
        _bar(240_000, 9, 11),
        _bar(300_000, 7, 10, close=7.5),
    ]

    decision, changed = evaluate_confirmed_structure_exit(
        position, meta, _snapshot(history, 360_100), 360_100, 7.4,
    )

    assert changed
    assert decision["type"] == EXIT_REASON
    assert decision["line"] == 8
    assert decision["trigger_close"] == 7.5
    assert meta[STATE_KEY]["pending"] == EXIT_REASON


def test_short_structure_exit_is_symmetric():
    position = _position("SHORT")
    meta = {}
    history = [
        _bar(60_000, 86, 90),
        _bar(120_000, 87, 92),
        _bar(180_000, 88, 91),
        _bar(240_000, 89, 91),
        _bar(300_000, 90, 93, close=92.5),
    ]

    decision, changed = evaluate_confirmed_structure_exit(
        position, meta, _snapshot(history, 360_100), 360_100, 92.4,
    )

    assert changed
    assert decision["type"] == EXIT_REASON
    assert decision["line"] == 92
    assert decision["trigger_close"] == 92.5


def test_structure_exit_uses_bar_close_not_intrabar_quote():
    position = _position("LONG")
    meta = {}
    history = [
        _bar(60_000, 10, 14),
        _bar(120_000, 8, 13),
        _bar(180_000, 9, 12),
        _bar(240_000, 9, 11),
        _bar(300_000, 8.5, 10, close=8.5),
    ]

    decision, _ = evaluate_confirmed_structure_exit(
        position, meta, _snapshot(history, 360_100), 360_100, 7.0,
    )

    assert decision is None


@pytest.mark.parametrize(
    "close,middle",
    [(7.5, 7.4), (7.4, 7.4)],
)
def test_structure_exit_waits_for_closed_kc_middle_breach(close, middle):
    position = _position("LONG")
    meta = {}
    history = [
        _bar(60_000, 10, 14),
        _bar(120_000, 8, 13),
        _bar(180_000, 9, 12),
        _bar(240_000, 9, 11),
        _bar(300_000, 7, 10, close=close),
    ]
    history[-1]["kc_middle"] = middle

    decision, _ = evaluate_confirmed_structure_exit(
        position, meta, _snapshot(history, 360_100), 360_100, 7.4,
    )

    assert decision is None


@pytest.mark.parametrize(
    "side,opening,close,middle",
    [
        ("LONG", 7.0, 7.5, 7.6),
        ("SHORT", 93.0, 92.5, 92.0),
    ],
)
def test_structure_exit_requires_adverse_body_close_beyond_midline(
    side, opening, close, middle,
):
    if side == "LONG":
        history = [
            _bar(60_000, 10, 14),
            _bar(120_000, 8, 13),
            _bar(180_000, 9, 12),
            _bar(240_000, 9, 11),
            _bar(300_000, 7, 10, close=close),
        ]
        history[-1]["o"] = opening
    else:
        history = [
            _bar(60_000, 86, 90),
            _bar(120_000, 87, 92),
            _bar(180_000, 88, 91),
            _bar(240_000, 89, 91),
            _bar(300_000, 90, 94, close=close),
        ]
        history[-1]["o"] = opening
    history[-1]["kc_middle"] = middle

    decision, _ = evaluate_confirmed_structure_exit(
        _position(side), {}, _snapshot(history, 360_100), 360_100, close,
    )

    assert decision is None


def test_two_completed_bar_grace_blocks_structural_exit():
    position = _position("LONG", opened_at=60.0)
    meta = {STATE_KEY: {
        "identity": ["LONG", 60.0, 100.0, 1.0],
        "line": 8.0,
        "line_bar_ms": 120_000,
        "last_closed_bar_ms": 120_000,
    }}
    history = [
        _bar(60_000, 10, 14),
        _bar(120_000, 9, 13),
        _bar(180_000, 7, 12, close=7.5),
    ]

    decision, _ = evaluate_confirmed_structure_exit(
        position, meta, _snapshot(history, 240_100), 240_100, 7.0,
    )

    assert decision is None


def test_pending_structure_exit_is_retried_for_same_bar():
    position = _position("LONG")
    meta = {STATE_KEY: {
        "identity": ["LONG", 60.0, 100.0, 1.0],
        "line": 8.0,
        "line_bar_ms": 120_000,
        "last_closed_bar_ms": 300_000,
        "pending": EXIT_REASON,
        "trigger_bar_ms": 300_000,
        "trigger_close": 7.5,
    }}
    history = [
        _bar(60_000, 10, 14),
        _bar(120_000, 8, 13),
        _bar(180_000, 9, 12),
        _bar(240_000, 9, 11),
        _bar(300_000, 7, 10, close=7.5),
    ]

    decision, changed = evaluate_confirmed_structure_exit(
        position, meta, _snapshot(history, 360_100), 360_100, 7.4,
    )

    assert changed
    assert decision["type"] == EXIT_REASON
    assert position[STATE_KEY]["pending"] == EXIT_REASON


def test_realtime_exit_path_submits_confirmed_structure_close(monkeypatch):
    from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit

    stamp = time.time() * 1000
    live_bar_ms = stamp // 60_000 * 60_000
    closed_bar_ms = live_bar_ms - 60_000
    history = [
        _bar(closed_bar_ms - 4 * 60_000, 10, 14),
        _bar(closed_bar_ms - 3 * 60_000, 8, 13),
        _bar(closed_bar_ms - 2 * 60_000, 9, 12),
        _bar(closed_bar_ms - 60_000, 9, 11),
        _bar(closed_bar_ms, 7, 10, close=7.5),
    ]
    snapshot = _snapshot(history, stamp)
    snapshot.update(
        live_bar_ms=live_bar_ms,
        closed_bar_ms=closed_bar_ms,
        snapshot_bar_id=closed_bar_ms,
    )
    position = {
        "symbol": "X",
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "open_timestamp": time.time() - 600,
        "entry_mode": "CHANNEL_SWING",
    }
    account = SimpleNamespace(
        positions={"X": position},
        position_meta={"X": {"entry_mode": "CHANNEL_SWING"}},
        save_state=Mock(),
        log=Mock(),
        close_position=AsyncMock(return_value=True),
    )
    engine = SimpleNamespace(
        is_running=True,
        account=account,
        _channel_exit_frames={"X": SimpleNamespace(empty=False)},
    )

    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit.enforce_hard_stop",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit.cached_tick_indicators",
        lambda *_args, **_kwargs: (snapshot, 1.0),
    )
    monkeypatch.setattr(
        "core.gates.holding_protection_gate.HoldingProtectionExitGate.evaluate",
        classmethod(lambda cls, *_args: (None, {})),
    )
    monkeypatch.setattr(
        "core.exits.peak_valley_exit.PeakValleyExit.evaluate",
        staticmethod(lambda *_args: (None, {})),
    )
    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit._evaluate_realtime_core_exit_gates",
        lambda *_args, **_kwargs: (None, False),
    )

    result = asyncio.run(
        enforce_realtime_profit_exit(engine, "X", 7.4, stamp)
    )

    assert result
    account.close_position.assert_awaited_once_with(
        "X", 7.4, f"Channel Swing {EXIT_REASON}", is_manual=True,
    )
    assert account.position_meta["X"][STATE_KEY]["pending"] == EXIT_REASON
