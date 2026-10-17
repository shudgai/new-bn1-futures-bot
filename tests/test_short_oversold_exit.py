import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from core.services.exits.short_oversold_exit import (
    REVERSAL_PARTIAL_TRIGGER,
    RATCHET_EXIT_TRIGGER,
    STATE_KEY,
    evaluate_short_oversold_exit,
)


def _position():
    return {
        "symbol": "CAP/USDT",
        "side": "SHORT",
        "entry_mode": "CHANNEL_SWING",
        "entry_price": 100.0,
        "open_timestamp": 60.0,
        "qty": 1.0,
        "margin": 10.0,
        "leverage": 10,
    }


def _bar(timestamp, opening, high, low, close, atr=1.0):
    return {
        "ms": float(timestamp),
        "o": float(opening),
        "h": float(high),
        "l": float(low),
        "c": float(close),
        "atr": float(atr),
    }


def _snapshot(*, quote, live_bar_ms, closed_bar_ms, history, live_low=None):
    result = {
        "reason": None,
        "atr": 1.0,
        "live_kc_lower": 95.0,
        "live_bar_ms": float(live_bar_ms),
        "closed_bar_ms": float(closed_bar_ms),
        "history_5": history,
        "ma5_history": [97.0, 97.0, 97.0],
        "ma15_history": [96.0, 96.0, 96.0],
    }
    if live_low is not None:
        result["live_low"] = float(live_low)
    return result


def _arm(position, meta, quote=94.5):
    history = [
        _bar(60_000, 96.0, 96.2, 95.2, 95.5),
        _bar(120_000, 95.5, 95.8, 94.8, 95.0),
    ]
    snapshot = _snapshot(
        quote=quote, live_bar_ms=180_000, closed_bar_ms=120_000,
        history=history, live_low=quote,
    )
    trigger, changed = evaluate_short_oversold_exit(
        position, meta, quote, snapshot,
    )
    return trigger, changed, snapshot


def test_short_extreme_below_lower_band_arms_at_half_atr_boundary():
    position, meta = _position(), {}

    trigger, changed, _ = _arm(position, meta, 94.5)

    assert trigger is None
    assert changed
    assert position[STATE_KEY]["armed"] is True
    assert position[STATE_KEY]["atr"] == 1.0
    assert position[STATE_KEY]["lowest_price"] == 94.5


def test_short_oversold_exit_does_not_arm_before_half_atr_breach():
    position, meta = _position(), {}
    _, changed, snapshot = _arm(position, meta, 94.51)

    assert changed is False
    assert STATE_KEY not in position

    trigger, changed = evaluate_short_oversold_exit(
        position, meta, 94.51, snapshot,
    )
    assert trigger is None
    assert changed is False


@pytest.mark.parametrize(
    "reversal_bar",
    [
        _bar(180_000, 94.0, 94.2, 93.0, 93.9),
        _bar(180_000, 93.9, 95.8, 93.8, 95.6),
    ],
)
def test_short_oversold_closed_wick_or_bullish_engulf_reduces_half(reversal_bar):
    position, meta = _position(), {}
    _arm(position, meta)
    history = [
        _bar(120_000, 95.5, 95.8, 94.8, 95.0),
        reversal_bar,
    ]
    snapshot = _snapshot(
        quote=93.3, live_bar_ms=240_000, closed_bar_ms=180_000,
        history=history, live_low=93.0,
    )

    trigger, changed = evaluate_short_oversold_exit(
        position, meta, 93.3, snapshot,
    )

    assert trigger == REVERSAL_PARTIAL_TRIGGER
    assert changed
    assert position[STATE_KEY]["partial_exit_pending"] is True
    assert position[STATE_KEY]["reversal_bar_ms"] == 180_000


def test_short_oversold_ratchet_exits_remaining_at_four_tenths_atr_rebound():
    position, meta = _position(), {}
    _arm(position, meta)
    snapshot = _snapshot(
        quote=93.4, live_bar_ms=240_000, closed_bar_ms=180_000,
        history=[
            _bar(120_000, 95.5, 95.8, 94.8, 95.0),
            _bar(180_000, 94.0, 94.2, 93.0, 93.9),
        ],
        live_low=93.0,
    )

    trigger, changed = evaluate_short_oversold_exit(
        position, meta, 93.4, snapshot,
    )

    assert trigger == RATCHET_EXIT_TRIGGER
    assert changed
    assert position[STATE_KEY]["full_exit_pending"] is True
    assert position[STATE_KEY]["lowest_price"] == 93.0


@pytest.mark.parametrize(
    "trigger_type",
    ["reversal", "ratchet"],
)
def test_bearish_ma_trend_holds_short_through_oversold_exit_signals(trigger_type):
    position, meta = _position(), {}
    _arm(position, meta)
    if trigger_type == "reversal":
        quote = 93.3
        history = [
            _bar(120_000, 95.5, 95.8, 94.8, 95.0),
            _bar(180_000, 94.0, 94.2, 93.0, 93.9),
        ]
        live_low = 93.0
    else:
        quote = 93.4
        history = [
            _bar(120_000, 95.5, 95.8, 94.8, 95.0),
            _bar(180_000, 94.0, 94.2, 93.0, 93.9),
        ]
        live_low = 93.0
    snapshot = _snapshot(
        quote=quote, live_bar_ms=240_000, closed_bar_ms=180_000,
        history=history, live_low=live_low,
    )
    snapshot["ma5_history"] = [95.0, 94.0, 93.0]
    snapshot["ma15_history"] = [100.0, 99.0, 98.0]

    trigger, changed = evaluate_short_oversold_exit(
        position, meta, quote, snapshot,
    )

    assert trigger is None
    assert changed
    assert position[STATE_KEY]["bearish_trend_hold_active"] is True
    assert position[STATE_KEY]["partial_exit_pending"] is False
    assert position[STATE_KEY]["full_exit_pending"] is False
    assert position[STATE_KEY]["lowest_price"] == 93.0


def test_missing_closed_ma_trend_data_fails_closed_for_oversold_exits():
    position, meta = _position(), {}
    _arm(position, meta)
    snapshot = _snapshot(
        quote=93.4, live_bar_ms=240_000, closed_bar_ms=180_000,
        history=[
            _bar(120_000, 95.5, 95.8, 94.8, 95.0),
            _bar(180_000, 94.0, 94.2, 93.0, 93.9),
        ],
        live_low=93.0,
    )
    snapshot.pop("ma5_history")
    snapshot.pop("ma15_history")

    trigger, _ = evaluate_short_oversold_exit(
        position, meta, 93.4, snapshot,
    )

    assert trigger is None
    assert position[STATE_KEY]["bearish_trend_hold_active"] is True


def test_oversold_exit_authorization_rejects_pending_exit_during_bearish_trend():
    from core.services.exits.authorized_position_exit import (
        automated_position_exit_allowed,
    )

    position = _position()
    position[STATE_KEY] = {
        "armed": True,
        "bearish_trend_hold_active": True,
        "reversal_confirmed": True,
        "partial_exit_pending": True,
        "full_exit_pending": True,
    }
    meta = {STATE_KEY: dict(position[STATE_KEY])}

    assert not automated_position_exit_allowed(
        "CAP/USDT",
        position,
        meta,
        f"Channel Swing {REVERSAL_PARTIAL_TRIGGER}",
        94.0,
        is_manual=True,
    )
    position["profit_lock_usdt_armed"] = True
    assert automated_position_exit_allowed(
        "CAP/USDT",
        position,
        meta,
        "NET_ROE_THREE_TIER_GIVEBACK",
        94.0,
        is_manual=True,
    )
    assert automated_position_exit_allowed(
        "CAP/USDT",
        position,
        meta,
        "HARD_STOP_LOSS",
        101.0,
        is_manual=True,
    )
    assert not automated_position_exit_allowed(
        "CAP/USDT",
        position,
        meta,
        f"Channel Swing {RATCHET_EXIT_TRIGGER}",
        94.0,
        is_manual=True,
    )


def test_account_poll_suppresses_non_profit_short_exit_in_bearish_trend(monkeypatch):
    from core.services.exits import entry_atr_protection

    now_ms = time.time() * 1000
    frame = pd.DataFrame([
        {
            "timestamp": now_ms - 60_000,
            "is_closed": True,
            "ma5": 90.0,
            "ma15": 100.0,
        },
        {
            "timestamp": now_ms,
            "is_closed": True,
            "ma5": 89.0,
            "ma15": 99.0,
        },
    ])
    frame.attrs["timeframe_ms"] = 60_000
    position = _position()
    account = SimpleNamespace(
        positions={"CAP/USDT": position},
        position_meta={"CAP/USDT": {}},
        channel_exit_frame_provider=lambda _symbol: frame,
        save_state=Mock(),
        close_position=AsyncMock(return_value=True),
        log=Mock(),
    )
    pivot_reason = "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
    decision = {
        "type": pivot_reason,
        "trigger": pivot_reason,
    }
    monkeypatch.setattr(
        "core.symbol_aliases.is_auto_entry_symbol", lambda _symbol: True,
    )
    monkeypatch.setattr(
        "core.services.exits.peak_trailing_exit.evaluate_peak_trailing",
        lambda *_args, **_kwargs: decision,
    )

    closed = asyncio.run(
        entry_atr_protection.enforce_atr_protection(account, "CAP/USDT", 94.0),
    )

    assert closed is False
    assert position["channel_short_bearish_trend_hold_active"] is True
    account.close_position.assert_not_awaited()


def test_auto_symbol_uses_confirmed_short_reversal_without_reverse_entry_signal(monkeypatch):
    from core.services.exits import realtime_profit_exit

    async def run():
        now_ms = time.time() * 1000
        position = _position()
        state = {
            "identity": "SHORT|60.000000000|100",
            "armed": True,
            "atr": 1.0,
            "armed_bar_ms": 180_000.0,
            "lowest_price": 94.0,
            "last_closed_bar_ms": 120_000.0,
            "partial_exit_done": False,
            "partial_exit_pending": True,
            "reversal_confirmed": True,
            "full_exit_pending": False,
            "bearish_trend_hold_active": False,
        }
        position[STATE_KEY] = dict(state)
        account = SimpleNamespace(
            positions={"CAP/USDT": position},
            position_meta={"CAP/USDT": {STATE_KEY: dict(state)}},
            save_state=Mock(),
            close_position=AsyncMock(return_value=True),
            partial_close_position=AsyncMock(return_value=True),
            log=Mock(),
        )
        engine = SimpleNamespace(
            account=account,
            is_running=True,
            _channel_exit_frames={"CAP/USDT": pd.DataFrame([{"placeholder": 1}])},
        )
        snapshot = _snapshot(
            quote=94.0, live_bar_ms=240_000, closed_bar_ms=180_000,
            history=[
                _bar(120_000, 95.5, 95.8, 94.8, 95.0),
                _bar(180_000, 94.0, 94.2, 93.0, 93.9),
            ],
            live_low=94.0,
        )
        monkeypatch.setattr(realtime_profit_exit, "enforce_hard_stop", AsyncMock(return_value=False))
        monkeypatch.setattr(
            realtime_profit_exit, "cached_tick_indicators",
            lambda *args: (snapshot, 1.0),
        )
        monkeypatch.setattr(
            "core.symbol_aliases.is_auto_entry_symbol", lambda _symbol: True,
        )

        closed = await realtime_profit_exit.enforce_realtime_profit_exit(
            engine, "CAP/USDT", 94.0, now_ms,
        )

        assert closed is True
        account.partial_close_position.assert_awaited_once_with(
            "CAP/USDT",
            94.0,
            "Channel Swing " + REVERSAL_PARTIAL_TRIGGER,
            fraction=0.5,
            is_manual=True,
        )
        account.close_position.assert_not_awaited()

    asyncio.run(run())


def test_auto_symbol_closes_short_on_trough_rebound_without_reverse_entry_signal(monkeypatch):
    from core.services.exits import realtime_profit_exit

    async def run():
        now_ms = time.time() * 1000
        position = _position()
        state = {
            "identity": "SHORT|60.000000000|100",
            "armed": True,
            "atr": 1.0,
            "armed_bar_ms": 180_000.0,
            "lowest_price": 93.0,
            "last_closed_bar_ms": 180_000.0,
            "partial_exit_done": True,
            "partial_exit_pending": False,
            "reversal_confirmed": True,
            "full_exit_pending": False,
            "bearish_trend_hold_active": False,
        }
        position[STATE_KEY] = dict(state)
        account = SimpleNamespace(
            positions={"CAP/USDT": position},
            position_meta={"CAP/USDT": {STATE_KEY: dict(state)}},
            save_state=Mock(),
            close_position=AsyncMock(return_value=True),
            partial_close_position=AsyncMock(return_value=True),
            log=Mock(),
        )
        engine = SimpleNamespace(
            account=account,
            is_running=True,
            _channel_exit_frames={"CAP/USDT": pd.DataFrame([{"placeholder": 1}])},
        )
        snapshot = _snapshot(
            quote=93.4, live_bar_ms=240_000, closed_bar_ms=180_000,
            history=[
                _bar(120_000, 95.5, 95.8, 94.8, 95.0),
                _bar(180_000, 94.0, 94.2, 93.0, 93.9),
            ],
            live_low=93.0,
        )
        monkeypatch.setattr(realtime_profit_exit, "enforce_hard_stop", AsyncMock(return_value=False))
        monkeypatch.setattr(
            realtime_profit_exit, "cached_tick_indicators",
            lambda *args: (snapshot, 1.0),
        )
        monkeypatch.setattr(
            "core.symbol_aliases.is_auto_entry_symbol", lambda _symbol: True,
        )

        closed = await realtime_profit_exit.enforce_realtime_profit_exit(
            engine, "CAP/USDT", 93.4, now_ms,
        )

        assert closed is True
        account.close_position.assert_awaited_once_with(
            "CAP/USDT",
            93.4,
            "Channel Swing " + RATCHET_EXIT_TRIGGER,
            is_manual=True,
        )
        account.partial_close_position.assert_not_awaited()

    asyncio.run(run())


def test_bearish_ma_trend_suppresses_pending_short_pivot_exit(monkeypatch):
    from core.services.exits import realtime_profit_exit
    from core.services.exits.strict_true_pivot import (
        CONFIRMED_PIVOT_PENDING_KEY,
    )

    async def run():
        now_ms = time.time() * 1000
        position = _position()
        position[CONFIRMED_PIVOT_PENDING_KEY] = {
            "identity": ["SHORT", 60.0, 100.0, 1.0],
            "reason": "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT",
            "details": {"pivot_bar_id": 120_000.0, "confirm_bar_id": 180_000.0},
        }
        account = SimpleNamespace(
            positions={"CAP/USDT": position},
            position_meta={"CAP/USDT": {}},
            save_state=Mock(),
            close_position=AsyncMock(return_value=True),
            partial_close_position=AsyncMock(return_value=True),
            log=Mock(),
        )
        engine = SimpleNamespace(
            account=account,
            is_running=True,
            _channel_exit_frames={"CAP/USDT": pd.DataFrame([{"placeholder": 1}])},
        )
        snapshot = _snapshot(
            quote=94.0, live_bar_ms=240_000, closed_bar_ms=180_000,
            history=[
                _bar(120_000, 95.5, 95.8, 94.8, 95.0),
                _bar(180_000, 94.0, 94.2, 93.0, 93.9),
            ],
            live_low=94.0,
        )
        snapshot["ma5_history"] = [95.0, 94.0, 93.0]
        snapshot["ma15_history"] = [100.0, 99.0, 98.0]
        monkeypatch.setattr(
            realtime_profit_exit, "enforce_hard_stop",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            realtime_profit_exit, "cached_tick_indicators",
            lambda *args: (snapshot, 1.0),
        )
        monkeypatch.setattr(
            "core.symbol_aliases.is_auto_entry_symbol", lambda _symbol: True,
        )

        closed = await realtime_profit_exit.enforce_realtime_profit_exit(
            engine, "CAP/USDT", 94.0, now_ms,
        )

        assert closed is False
        account.close_position.assert_not_awaited()
        account.partial_close_position.assert_not_awaited()

    asyncio.run(run())


def test_paper_partial_close_updates_only_half_of_position(monkeypatch):
    from core.paper_account import PaperAccount

    monkeypatch.setattr(
        "core.symbol_aliases.is_auto_entry_symbol", lambda _symbol: True,
    )
    account = object.__new__(PaperAccount)
    position = _position()
    state = {
        "identity": "SHORT|60.000000000|100",
        "armed": True,
        "reversal_confirmed": True,
        "partial_exit_pending": True,
        "partial_exit_done": False,
        "bearish_trend_hold_active": False,
    }
    position[STATE_KEY] = dict(state)
    account.positions = {"CAP/USDT": position}
    account.position_meta = {"CAP/USDT": {STATE_KEY: dict(state)}}
    account.closing_lock = set()
    account.pending_limit_orders = {}
    account.balance = 100.0
    account.available_balance = 100.0
    account.realized_pnl = 0.0
    account.trades = []
    account.save_state = Mock()
    account.log = Mock()

    closed = asyncio.run(
        account.partial_close_position(
            "CAP/USDT",
            99.0,
            f"Channel Swing {REVERSAL_PARTIAL_TRIGGER}",
            fraction=0.5,
            is_manual=True,
        )
    )

    assert closed is True
    assert account.positions["CAP/USDT"]["qty"] == pytest.approx(0.5)
    assert account.positions["CAP/USDT"]["margin"] == pytest.approx(5.0)
    assert account.position_meta["CAP/USDT"][STATE_KEY]["partial_exit_done"] is True
    assert account.trades[0]["action"] == "PARTIAL_CLOSE_SHORT"
    assert account.trades[0]["status"] == "PARTIAL_CLOSED"


def test_testnet_partial_close_requires_confirmed_reduce_only_fill(monkeypatch):
    from core.gates.trend_riding_strategy import AntiSlippageOrderExecutor
    from core.testnet_account import BinanceTestnetAccount

    monkeypatch.setattr(
        "core.symbol_aliases.is_auto_entry_symbol", lambda _symbol: True,
    )
    monkeypatch.setattr(
        AntiSlippageOrderExecutor,
        "execute_confirmed_limit_close",
        AsyncMock(return_value=(
            True,
            {"id": "order-1", "filled": 0.5, "average": 99.0, "status": "CLOSED"},
        )),
    )
    account = object.__new__(BinanceTestnetAccount)
    position = _position()
    state = {
        "identity": "SHORT|60.000000000|100",
        "armed": True,
        "reversal_confirmed": True,
        "partial_exit_pending": True,
        "partial_exit_done": False,
        "bearish_trend_hold_active": False,
    }
    position[STATE_KEY] = dict(state)
    account.positions = {"CAP/USDT": position}
    account.position_meta = {"CAP/USDT": {STATE_KEY: dict(state)}}
    account.closing_lock = set()
    account.pending_limit_orders = {}
    account.realized_pnl = 0.0
    account.trades = []
    account.save_state = Mock()
    account.log = Mock()
    account._amount_to_precision = lambda _symbol, qty: str(qty)
    account._cancel_all_orders = AsyncMock()
    account.refresh = AsyncMock()

    closed = asyncio.run(
        account.partial_close_position(
            "CAP/USDT",
            99.0,
            f"Channel Swing {REVERSAL_PARTIAL_TRIGGER}",
            fraction=0.5,
            is_manual=True,
        )
    )

    assert closed is True
    assert account.positions["CAP/USDT"]["qty"] == pytest.approx(0.5)
    assert account.position_meta["CAP/USDT"][STATE_KEY]["partial_exit_done"] is True
    assert account.trades[0]["exchange_order_id"] == "order-1"
    account.refresh.assert_awaited_once_with(force=True)
