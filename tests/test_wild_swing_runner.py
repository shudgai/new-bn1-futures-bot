import asyncio

import pandas as pd
import pytest

from core.services.symbol_runner import process_single_symbol_runner
from core.services.strategies.wild_swing_strategy import WildSwingStrategy
from test_wild_swing_strategy import _frame


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

    next_bar = first.iloc[-1].copy()
    next_bar["timestamp"] = float(first.iloc[-1]["timestamp"]) + 60_000
    next_bar.update({
        "open": 99.2,
        "high": 99.3,
        "low": 98.8,
        "close": 98.9,
        "kc_middle": 99.8,
        "ma15": 100.0,
        "ma5": 99.7,
    })
    next_frame = pd.concat([first, next_bar.to_frame().T], ignore_index=True)
    asyncio.run(
        process_single_symbol_runner(
            engine, "LOBSTER/USDT", 60, None, False,
            exit_frame=next_frame, exit_quote=99.0,
        )
    )

    assert len(engine.entries) == 1
    assert engine.entries[0]["entry_mode"] == "WILD_SWING"
    assert engine.entries[0]["meta_dict"]["entry_signal_mode"] == "CONTINUATION"
    assert engine.entries[0]["frame"].iloc[-1]["timestamp"] == next_bar["timestamp"]


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
    next_bar = first.iloc[-1].copy()
    next_bar["timestamp"] = float(first.iloc[-1]["timestamp"]) + 60_000
    next_bar.update({
        "open": 99.2, "high": 99.3, "low": 98.8, "close": 98.9,
        "kc_middle": 99.8, "ma15": 100.0, "ma5": 99.7,
    })
    next_frame = pd.concat([first, next_bar.to_frame().T], ignore_index=True)
    asyncio.run(
        process_single_symbol_runner(
            engine, "LOBSTER/USDT", 60, None, False,
            exit_frame=next_frame, exit_quote=99.0,
        )
    )

    assert qwen_calls == []
    assert engine.entries == []


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_post_close_reentry_waits_for_a_new_closed_one_minute_bar(side):
    engine = FakeEngine()
    symbol = "LOBSTER/USDT"
    frame = _frame(side)
    frame["atr"] = 2.0
    frame["timestamp"] = [index * 60_000 for index in range(len(frame))]
    exit_bar_id = float(frame.iloc[-1]["timestamp"])
    strategy = WildSwingStrategy()
    strategy.record_exit(symbol, side, float(frame.iloc[-1]["close"]), "TEST_EXIT", exit_bar_id)
    engine._wild_swing_strategy = strategy

    next_bar = frame.iloc[-1].copy()
    next_bar["timestamp"] = exit_bar_id + 60_000
    if side == "SHORT":
        next_bar.update({
            "open": 99.2, "high": 99.3, "low": 98.8, "close": 98.9,
            "kc_middle": 99.8, "ma15": 100.0, "ma5": 99.7,
        })
    else:
        next_bar.update({
            "open": 100.8, "high": 101.3, "low": 100.7, "close": 101.2,
            "kc_middle": 100.2, "ma15": 100.0, "ma5": 100.1,
        })
    next_bar["is_closed"] = False
    live_frame = pd.concat([frame, next_bar.to_frame().T], ignore_index=True)

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
