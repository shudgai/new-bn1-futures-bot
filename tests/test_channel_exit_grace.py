import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core import config
from core.services.exits.entry_atr_protection import (
    channel_strategy_exit_grace_active,
    enforce_atr_protection,
)
from core.services.exits.peak_trailing_exit import STATE_KEY
from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "SUI/USDT"])
def test_lobster_and_sui_share_channel_strategy_exit_grace(symbol, monkeypatch):
    monkeypatch.setattr(config, "MIN_HOLD_SEC_FOR_STRATEGY_EXIT", 60)
    position = {
        "entry_mode": "CHANNEL_SWING",
        "open_timestamp": 100.0,
    }

    assert channel_strategy_exit_grace_active(position, now=159.0)
    assert not channel_strategy_exit_grace_active(position, now=160.0)


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "SUI/USDT"])
def test_account_update_does_not_close_strategy_exit_during_grace(
    symbol, monkeypatch
):
    monkeypatch.setattr(config, "MIN_HOLD_SEC_FOR_STRATEGY_EXIT", 60)
    position = {
        "symbol": symbol,
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "open_timestamp": time.time() - 5,
        "entry_atr": 1.0,
        "entry_mode": "CHANNEL_SWING",
    }
    account = SimpleNamespace(
        positions={symbol: position},
        position_meta={symbol: {}},
        save_state=Mock(),
        log=Mock(),
        close_position=AsyncMock(),
    )

    def trigger_strategy_exit(pos, *_args, **_kwargs):
        pos[STATE_KEY].update(
            pending="EXIT_PEAK_PULLBACK_PRESSURE",
            trigger="EXIT_PEAK_PULLBACK_PRESSURE",
        )
        return {
            "type": "EXIT_PEAK_PULLBACK_PRESSURE",
            "trigger": "EXIT_PEAK_PULLBACK_PRESSURE",
        }

    monkeypatch.setattr(
        "core.services.exits.peak_trailing_exit.evaluate_peak_trailing",
        trigger_strategy_exit,
    )

    assert not asyncio.run(enforce_atr_protection(account, symbol, 99.0))
    account.close_position.assert_not_awaited()
    assert "pending" not in position[STATE_KEY]


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "SUI/USDT"])
def test_realtime_strategy_exit_waits_but_account_hard_stop_still_runs(
    symbol, monkeypatch
):
    monkeypatch.setattr(config, "MIN_HOLD_SEC_FOR_STRATEGY_EXIT", 60)
    position = {
        "symbol": symbol,
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "open_timestamp": time.time() - 5,
        "entry_mode": "CHANNEL_SWING",
    }
    account = SimpleNamespace(
        positions={symbol: position},
        position_meta={symbol: {}},
        save_state=Mock(),
        log=Mock(),
        close_position=AsyncMock(),
    )
    engine = SimpleNamespace(
        is_running=True,
        account=account,
        _channel_exit_frames={},
    )
    hard_stop = AsyncMock(return_value=False)

    def trigger_strategy_exit(*_args, **_kwargs):
        return {
            "type": "EXIT_PEAK_PULLBACK_PRESSURE",
            "trigger": "EXIT_PEAK_PULLBACK_PRESSURE",
        }

    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit.enforce_hard_stop",
        hard_stop,
    )
    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit.cached_tick_indicators",
        lambda *_args, **_kwargs: ({"quote_ms": time.time() * 1000}, 1.0),
    )
    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit.PureTrendStrategyV2.evaluate_anti_whipsaw_profit_lock",
        trigger_strategy_exit,
    )

    assert not asyncio.run(
        enforce_realtime_profit_exit(engine, symbol, 99.0, time.time() * 1000)
    )
    hard_stop.assert_awaited_once()
    account.close_position.assert_not_awaited()


@pytest.mark.parametrize(
    ("trigger", "should_close"),
    [
        ("EXIT_CHANNEL_PEAK_PROFIT_LOCK", True),
        ("EXIT_CHANNEL_PEAK_WICK_REVERSAL", True),
        ("EXIT_LONG_ON_CONFIRMED_MIDLINE_BREAK", True),
        ("EXIT_SHORT_ON_CONFIRMED_MIDLINE_BREAK", True),
        ("MA5_TRUE_PEAK_REVERSAL", False),
        ("THREE_POINT_PIVOT", False),
        ("EXIT_LONG_ON_FRACTAL_PEAK", False),
        ("EXIT_SHORT_ON_FRACTAL_VALLEY", False),
        ("EXIT_LONG_ON_UPPER_WICK_REJECTION", False),
        ("EXIT_SHORT_ON_CONFIRMED_TROUGH", False),
        ("NET_ROE_THREE_TIER_GIVEBACK_TIER_2", False),
        ("EXIT_PEAK_PULLBACK_PRESSURE", False),
        ("KC_CHANNEL_RETURN", False),
        ("EXIT_PROFIT_LOCK_FLOOR", False),
        ("DOJI_REVERSAL_EXIT", False),
        ("WATERFALL_DROP", False),
        ("TWO_CLOSED_ADVERSE_ABNORMAL", False),
    ],
)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_realtime_exit_allowlist_is_symbol_independent(
    trigger, should_close, side, monkeypatch,
):
    symbol = "SUI/USDT"
    position = {
        "symbol": symbol,
        "side": side,
        "entry_price": 100.0,
        "qty": 1.0,
        "open_timestamp": time.time() - 3600,
        "entry_mode": "CHANNEL_SWING",
    }
    account = SimpleNamespace(
        positions={symbol: position},
        position_meta={symbol: {}},
        save_state=Mock(),
        log=Mock(),
        close_position=AsyncMock(),
    )
    engine = SimpleNamespace(
        is_running=True,
        account=account,
        _channel_exit_frames={symbol: SimpleNamespace(empty=False)},
    )
    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit.enforce_hard_stop",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit.cached_tick_indicators",
        lambda *_args, **_kwargs: (
            {"quote_ms": time.time() * 1000, "snapshot_bar_id": 0.}, 1.0,
        ),
    )
    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit._evaluate_realtime_core_exit_gates",
        lambda *_args, **_kwargs: (trigger, False),
    )
    monkeypatch.setattr(
        "core.gates.holding_protection_gate.HoldingProtectionExitGate.evaluate",
        classmethod(lambda cls, *args: (None, {})),
    )
    monkeypatch.setattr(
        "core.exits.peak_valley_exit.PeakValleyExit.evaluate",
        staticmethod(lambda *args: (None, {})),
    )
    monkeypatch.setattr(
        "core.gates.holding_protection_gate.HoldingProtectionExitGate.validate_exit",
        classmethod(lambda cls, *args, **kwargs: (
            True, trigger, kwargs.get("details", {}),
        )),
    )

    closed = asyncio.run(
        enforce_realtime_profit_exit(engine, symbol, 101.0, time.time() * 1000)
    )

    assert closed is should_close, account.log.call_args_list
    assert account.close_position.await_count == int(should_close)
