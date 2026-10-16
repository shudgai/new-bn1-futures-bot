import asyncio

import pandas as pd
import pytest

from core.services.symbol_runner import process_single_symbol_runner
from core.services.strategies.wild_swing_strategy import WildSwingStrategy
from test_wild_swing_strategy import (
    _frame, _pivot_cross_frame, _profit_lock_continuation_frame,
)


@pytest.fixture(autouse=True)
def approve_chart_vision_for_runner_tests(monkeypatch):
    from core.vision import chart_guard
    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    async def approve(*args, **kwargs):
        return True, "test approved"

    monkeypatch.setenv("ENABLE_VISION_GUARD", "true")
    monkeypatch.setenv("USE_LOCAL_QWEN", "true")
    monkeypatch.setenv("VISION_FAIL_OPEN", "0")
    monkeypatch.setattr(chart_guard, "evaluate_qwen_entry_vision", approve)
    monkeypatch.setattr(
        MacroCandleStructureGate,
        "can_open",
        lambda *args, **kwargs: (True, "test macro pass"),
    )


class FakeAccount:
    def __init__(self):
        self.positions = {}
        self.closing_lock = set()
        self.pending_limit_orders = {}
        self.trades = []


class FakeEngine:
    def __init__(self):
        self.account = FakeAccount()
        self.tickers = {"LOBSTER/USDT": 99.0}
        self.entries = []

    async def execute_dual_tf_entry(self, **kwargs):
        self.entries.append(kwargs)
        self.account.positions[kwargs["symbol"]] = {"side": kwargs["side"]}
        return True


def test_runner_anchors_state_on_restart_then_evaluates_next_closed_one_minute_bar():
    engine = FakeEngine()
    first = _frame("SHORT")
    first["atr"] = 2.0
    first["timestamp"] = [index * 60_000 for index in range(len(first))]

    asyncio.run(
        process_single_symbol_runner(
            engine, "LOBSTER/USDT", 0, None, False,
            exit_frame=first, exit_quote=99.0,
        )
    )
    assert engine.entries == []

    next_frame = _pivot_cross_frame("SHORT")
    next_frame["timestamp"] += 720_000
    next_frame["atr"] = 2.0
    asyncio.run(
        process_single_symbol_runner(
            engine, "LOBSTER/USDT", 60, None, False,
            exit_frame=next_frame, exit_quote=99.0,
        )
    )

    assert len(engine.entries) == 1
    assert engine.entries[0]["entry_mode"] == "WILD_SWING"
    assert engine.entries[0]["meta_dict"]["entry_signal_mode"] == "REVERSAL"
    assert engine.entries[0]["frame"].iloc[-1]["timestamp"] == next_frame.iloc[-1]["timestamp"]


def test_runner_does_not_send_chart_for_a_macro_gate_veto(monkeypatch):
    from core.vision import chart_guard
    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    qwen_calls = []

    async def qwen(*args, **kwargs):
        qwen_calls.append((args, kwargs))
        return True, "unexpected approval"

    monkeypatch.setattr(chart_guard, "evaluate_qwen_entry_vision", qwen)
    monkeypatch.setattr(
        MacroCandleStructureGate,
        "can_open",
        lambda *args, **kwargs: (False, "TEST_VETO"),
    )
    engine = FakeEngine()
    first = _frame("SHORT")
    first["atr"] = 2.0
    first["timestamp"] = [index * 60_000 for index in range(len(first))]
    asyncio.run(
        process_single_symbol_runner(
            engine, "LOBSTER/USDT", 0, None, False,
            exit_frame=first, exit_quote=99.0,
        )
    )
    next_frame = _pivot_cross_frame("SHORT")
    next_frame["timestamp"] += 720_000
    next_frame["atr"] = 2.0
    asyncio.run(
        process_single_symbol_runner(
            engine, "LOBSTER/USDT", 60, None, False,
            exit_frame=next_frame, exit_quote=99.0,
        )
    )

    assert qwen_calls == []
    assert engine.entries == []


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_runner_reconstructs_profit_lock_continuation_from_account_trade(side):
    engine = FakeEngine()
    symbol = "LOBSTER/USDT"
    timestamp_offset = 1_740_000_000_000
    close_id = timestamp_offset + 600_001
    engine.account.trades.insert(0, {
        "symbol": symbol,
        "action": f"CLOSE_{side}",
        "entry_mode": "WILD_SWING",
        "reason": "Wild Swing EXIT_BY_PROFIT_LOCK",
        "id": close_id,
    })
    strategy = WildSwingStrategy()
    strategy.state_for(symbol)
    engine._wild_swing_strategy = strategy
    frame = _profit_lock_continuation_frame(side)
    frame["timestamp"] += timestamp_offset
    frame["atr"] = 2.0

    asyncio.run(
        process_single_symbol_runner(
            engine, symbol, 60, None, False,
            exit_frame=frame, exit_quote=float(frame.iloc[-1]["close"]),
        )
    )

    assert len(engine.entries) == 1
    signal = engine.entries[0]["meta_dict"]
    assert signal["reason"] == "PROFIT_LOCK_CONTINUATION_ENTRY"
    assert signal["profit_lock_close_id"] == close_id
    assert signal["side"] == side


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_post_close_reentry_waits_for_a_new_closed_one_minute_bar(side):
    engine = FakeEngine()
    symbol = "LOBSTER/USDT"
    frame = _pivot_cross_frame(side)
    frame["atr"] = 2.0
    frame["timestamp"] = [index * 60_000 for index in range(len(frame))]
    exit_bar_id = float(frame.iloc[-1]["timestamp"])
    strategy = WildSwingStrategy()
    strategy.record_exit(symbol, side, float(frame.iloc[-1]["close"]), "TEST_EXIT", exit_bar_id)
    engine._wild_swing_strategy = strategy

    next_frame = _pivot_cross_frame(side)
    next_frame["timestamp"] += 720_000
    next_frame["atr"] = 2.0
    live_frame = next_frame.copy()
    live_frame.loc[live_frame.index[-1], "is_closed"] = False
    next_bar = live_frame.iloc[-1]

    asyncio.run(
        process_single_symbol_runner(
            engine, symbol, 60, None, False, exit_frame=live_frame,
            exit_quote=float(next_bar["close"]),
        )
    )
    assert engine.entries == []

    live_frame.loc[live_frame.index[-1], "is_closed"] = True
    asyncio.run(
        process_single_symbol_runner(
            engine, symbol, 120, None, False, exit_frame=live_frame,
            exit_quote=float(next_bar["close"]),
        )
    )
    assert len(engine.entries) == 1
    assert engine.entries[0]["side"] == side
    assert engine.entries[0]["meta_dict"]["entry_signal_mode"] == "REVERSAL"
