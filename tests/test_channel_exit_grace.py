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
    ("symbol", "trigger", "should_close"),
    [
        ("SUI/USDT", "MA5_TRUE_PEAK_REVERSAL", False),
        ("SUI/USDT", "EXIT_PEAK_PULLBACK_PRESSURE", False),
        ("SUI/USDT", "KC_CHANNEL_RETURN", False),
        ("SUI/USDT", "EXIT_PROFIT_LOCK_FLOOR", False),
        ("SUI/USDT", "DOJI_REVERSAL_EXIT", False),
        ("SUI/USDT", "THREE_POINT_PIVOT", True),
        ("SUI/USDT", "WATERFALL_DROP", True),
        ("SUI/USDT", "TWO_CLOSED_ADVERSE_ABNORMAL", True),
        ("龙虾/USDT", "MA5_TRUE_PEAK_REVERSAL", False),
        ("龙虾/USDT", "EXIT_PEAK_PULLBACK_PRESSURE", False),
        ("龙虾/USDT", "EXIT_PROFIT_LOCK_FLOOR", False),
        ("龙虾/USDT", "DOJI_REVERSAL_EXIT", False),
        ("龙虾/USDT", "THREE_POINT_PIVOT", True),
        ("龙虾/USDT", "WATERFALL_DROP", True),
        ("龙虾/USDT", "TWO_CLOSED_ADVERSE_ABNORMAL", True),
    ],
)
def test_pivot_only_symbols_reject_locks_and_pullbacks(
    symbol, trigger, should_close, monkeypatch
):
    position = {
        "symbol": symbol,
        "side": "LONG",
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
        _channel_exit_frames={},
    )
    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit.enforce_hard_stop",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit.cached_tick_indicators",
        lambda *_args, **_kwargs: ({"quote_ms": time.time() * 1000}, 1.0),
    )
    def trigger_strategy_exit(_self, position, *_args, **_kwargs):
        position[STATE_KEY].update(peak_price=100.0, peak_net_pnl=0.0)
        return {"type": "EXIT_ADVERSE_ABNORMAL_BODY", "trigger": trigger}

    from core.services.exits.peak_trailing_exit import STATE_KEY
    monkeypatch.setattr(
        "core.services.exits.realtime_profit_exit.PureTrendStrategyV2.evaluate_anti_whipsaw_profit_lock",
        trigger_strategy_exit,
    )

    closed = asyncio.run(
        enforce_realtime_profit_exit(engine, symbol, 101.0, time.time() * 1000)
    )

    assert closed is should_close
    assert account.close_position.await_count == int(should_close)
