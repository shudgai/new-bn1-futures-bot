import asyncio
import json

import pandas as pd
import pytest

from core.services.symbol_runner import (
    _process_wild_swing_symbol,
    process_single_symbol_runner,
)
from core.services.strategies.wild_swing_strategy import WildSwingStrategy
from test_wild_swing_strategy import (
    _frame, _pivot_cross_frame, _profit_lock_continuation_frame,
)
from test_breakout_continuation_entry_gate import _numeric_wild_swing_frame


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


class FakeWaitEntryAuthority:
    def arbitrate(self, authorities):
        sides = {item["side"] for item in authorities}
        if len(sides) > 1:
            return {"status": "BLOCKED_OPPOSITE_AUTHORITIES"}
        if not authorities:
            return {"status": "NO_AUTHORITY"}
        return {"status": "ONE_ORDER", "winner": authorities[0]}

    def pending_events(self, symbol):
        return []

    def acknowledge_events(self, event_ids):
        return None

    def unknown_claims(self, symbol):
        return []


class FakeEngine:
    def __init__(self):
        self.account = FakeAccount()
        self.wait_entry_authority = FakeWaitEntryAuthority()
        self.tickers = {"LOBSTER/USDT": 99.0}
        self.entries = []

    async def execute_dual_tf_entry(self, **kwargs):
        self.entries.append(kwargs)
        self.account.positions[kwargs["symbol"]] = {"side": kwargs["side"]}
        return True


@pytest.mark.parametrize("symbol", ["LOBSTER/USDT", "1000PEPE/USDT"])
def test_runner_ignores_old_vision_lock_and_does_not_call_qwen(
    monkeypatch, symbol,
):
    from types import SimpleNamespace

    from core.vision.chart_guard import DynamicVisionGatekeeper

    engine = FakeEngine()
    first = _frame("SHORT")
    first["atr"] = 2.0
    first["timestamp"] = [index * 60_000 for index in range(len(first))]

    asyncio.run(
        process_single_symbol_runner(
            engine, symbol, 0, None, False,
            exit_frame=first, exit_quote=99.0,
        )
    )
    assert engine.entries == []

    vision_calls = []

    async def approve(frame, side, mode=None):
        vision_calls.append((side, mode))
        return True, "test approved"

    from core.vision import chart_guard

    monkeypatch.setattr(chart_guard, "evaluate_qwen_entry_vision", approve)
    next_frame = _pivot_cross_frame("SHORT")
    next_frame["timestamp"] += 720_000
    next_frame["atr"] = 2.0
    next_frame.loc[10, "ma15"] = 99.8
    next_frame.loc[11, "ma15"] = 100.0
    next_frame.loc[10, "kc_middle"] = 100.0
    next_frame.loc[11, "kc_middle"] = 99.9
    engine._wild_swing_vision_gate = DynamicVisionGatekeeper()
    engine._wild_swing_vision_gate.record_rejection(
        symbol,
        SimpleNamespace(**next_frame.iloc[-1].to_dict()),
        "SHORT",
    )
    asyncio.run(
        process_single_symbol_runner(
            engine, symbol, 60, None, False,
            exit_frame=next_frame, exit_quote=99.0,
        )
    )

    assert len(engine.entries) == 1
    assert engine.entries[0]["entry_mode"] == "WILD_SWING"
    assert engine.entries[0]["meta_dict"]["entry_signal_mode"] == "CROSSOVER"
    assert engine.entries[0]["frame"].iloc[-1]["timestamp"] == next_frame.iloc[-1]["timestamp"]
    assert vision_calls == []
    assert symbol in engine._wild_swing_vision_gate._rejections


def test_wild_swing_vision_rejection_state_is_isolated_by_symbol(monkeypatch):
    from types import SimpleNamespace

    from core.vision.chart_guard import DynamicVisionGatekeeper

    engine = FakeEngine()
    engine._wild_swing_vision_gate = DynamicVisionGatekeeper()
    rejected_bar = SimpleNamespace(high=101., low=99., close=100.)
    engine._wild_swing_vision_gate.record_rejection(
        "1000PEPE/USDT", rejected_bar, "SHORT",
    )
    first = _frame("SHORT")
    first["atr"] = 2.0
    first["timestamp"] = [index * 60_000 for index in range(len(first))]
    asyncio.run(
        process_single_symbol_runner(
            engine, "LOBSTER/USDT", 0, None, False,
            exit_frame=first, exit_quote=99.,
        )
    )
    next_frame = _pivot_cross_frame("SHORT")
    next_frame["timestamp"] += 720_000
    next_frame["atr"] = 2.0
    next_frame.loc[10, "kc_middle"] = 100.0
    next_frame.loc[11, "kc_middle"] = 99.9
    next_frame.loc[10, "ma15"] = 99.8
    next_frame.loc[11, "ma15"] = 100.0
    asyncio.run(
        process_single_symbol_runner(
            engine, "LOBSTER/USDT", 60, None, False,
            exit_frame=next_frame, exit_quote=99.,
        )
    )

    assert len(engine.entries) == 1
    assert engine.entries[0]["symbol"] == "LOBSTER/USDT"
    assert "1000PEPE/USDT" in engine._wild_swing_vision_gate._rejections
    assert "LOBSTER/USDT" not in engine._wild_swing_vision_gate._rejections


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


def test_runner_macro_veto_is_independent_of_dynamic_vision_lock(monkeypatch):
    from types import SimpleNamespace

    from core.vision.chart_guard import DynamicVisionGatekeeper
    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    monkeypatch.setattr(
        MacroCandleStructureGate,
        "can_open",
        lambda *args, **kwargs: (False, "TEST_VETO"),
    )
    engine = FakeEngine()
    engine._wild_swing_vision_gate = DynamicVisionGatekeeper()
    first = _frame("SHORT")
    first["atr"] = 2.0
    first["timestamp"] = [index * 60_000 for index in range(len(first))]
    asyncio.run(
        process_single_symbol_runner(
            engine, "LOBSTER/USDT", 0, None, False,
            exit_frame=first, exit_quote=99.0,
        )
    )
    engine._wild_swing_vision_gate.record_rejection(
        "LOBSTER/USDT",
        SimpleNamespace(high=100.2, low=98.8, close=99.5),
        "SHORT",
    )

    frame = _pivot_cross_frame("SHORT")
    frame["timestamp"] += 720_000
    frame["atr"] = 2.0
    asyncio.run(
        process_single_symbol_runner(
            engine, "LOBSTER/USDT", 60, None, False,
            exit_frame=frame, exit_quote=99.0,
        )
    )

    assert engine.entries == []
    assert "LOBSTER/USDT" in engine._wild_swing_vision_gate._rejections


@pytest.mark.parametrize(
    "side,pattern",
    [
        ("LONG", "INITIAL_2BAR_BREAKOUT"),
        ("SHORT", "CONTINUATION_OUTSIDE_KC"),
    ],
)
def test_runner_bypasses_vision_and_dynamic_lock_for_numeric_patterns(
    monkeypatch, side, pattern,
):
    from types import SimpleNamespace

    from core.vision.chart_guard import DynamicVisionGatekeeper

    qwen_calls = []

    async def reject_if_called(*args, **kwargs):
        qwen_calls.append((args, kwargs))
        return False, "unexpected visual review"

    from core.vision import chart_guard

    monkeypatch.setattr(chart_guard, "evaluate_qwen_entry_vision", reject_if_called)
    engine = FakeEngine()
    frame = _numeric_wild_swing_frame(side, pattern)
    engine._wild_swing_vision_gate = DynamicVisionGatekeeper()
    engine._wild_swing_vision_gate.record_rejection(
        "LOBSTER/USDT",
        SimpleNamespace(high=110.0, low=90.0, close=100.0),
        side,
    )

    asyncio.run(_process_wild_swing_symbol(
        engine, "LOBSTER/USDT", frame.iloc[:-1],
        float(frame.iloc[-2]["close"]), False, False,
    ))
    asyncio.run(_process_wild_swing_symbol(
        engine, "LOBSTER/USDT", frame,
        float(frame.iloc[-1]["close"]), False, False,
    ))

    assert len(engine.entries) == 1
    assert engine.entries[0]["side"] == side
    assert qwen_calls == []


@pytest.mark.parametrize(
    "side,open_price,high,low,close",
    [
        ("LONG", 106.5, 106.6, 106.5, 106.55),
        ("SHORT", 93.5, 93.5, 93.4, 93.45),
    ],
)
def test_runner_waits_for_live_bar_close_before_outside_continuation(
    side, open_price, high, low, close,
):
    frame = _numeric_wild_swing_frame(side, "CONTINUATION_OUTSIDE_KC")
    live = frame.iloc[-1].copy()
    live["timestamp"] += 60_000
    live["is_closed"] = False
    live["open"], live["high"], live["low"], live["close"] = (
        open_price, high, low, close,
    )
    frame = pd.concat([frame, live.to_frame().T], ignore_index=True)
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    engine = FakeEngine()

    asyncio.run(
        _process_wild_swing_symbol(
            engine, "LOBSTER/USDT", frame, close, False, False,
        )
    )

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
    assert signal["reason"] == "CONFIRMED_2BAR_BREAKOUT_ENTRY"
    assert signal["structure_confirmation"] == "CONFIRMED_2BAR_BREAKOUT"
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
    if side == "LONG":
        next_frame.loc[10, "kc_middle"] = 100.0
        next_frame.loc[11, "kc_middle"] = 100.1
    else:
        next_frame.loc[10, "kc_middle"] = 100.0
        next_frame.loc[11, "kc_middle"] = 99.9
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
    assert engine.entries[0]["meta_dict"]["entry_signal_mode"] == "CROSSOVER"


def test_runner_does_not_close_on_ratchet_alone():
    class Account(FakeAccount):
        def __init__(self):
            super().__init__()
            self.close_calls = []
            self.last_closed_price = {}

        async def close_position(self, symbol, price, reason, **kwargs):
            self.close_calls.append((symbol, price, reason, kwargs))
            self.positions.pop(symbol, None)
            self.last_closed_price[symbol] = price
            return True

    class Engine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.account = Account()

    engine = Engine()
    symbol = "LOBSTER/USDT"
    engine.account.positions[symbol] = {
        "side": "LONG",
        "entry_price": 100.0,
        "entry_mode": "WILD_SWING",
    }
    strategy = WildSwingStrategy()
    strategy.reset_symbol(symbol, 720_000.0)
    engine._wild_swing_strategy = strategy
    frame = _frame("LONG")
    frame["timestamp"] = [index * 60_000 for index in range(len(frame))]

    asyncio.run(_process_wild_swing_symbol(
        engine, symbol, frame, 101.0, False, False,
    ))

    assert engine.account.close_calls == []
    assert symbol in engine.account.positions


def test_runner_persists_and_retries_kc_true_extreme_close_after_restore():
    from core.services.exits.kc_true_extreme_exit import STATE_KEY

    class Account(FakeAccount):
        def __init__(self, symbol, position):
            super().__init__()
            self.positions[symbol] = position
            self.position_meta = {symbol: {}}
            self.close_results = [False, True]
            self.close_calls = []
            self.last_closed_price = {}
            self.save_count = 0

        def save_state(self):
            self.save_count += 1

        async def close_position(self, symbol, price, reason, **_kwargs):
            self.close_calls.append((symbol, price, reason))
            closed = self.close_results.pop(0)
            if closed:
                self.positions.pop(symbol, None)
                self.position_meta.pop(symbol, None)
                self.last_closed_price[symbol] = price
            return closed

    symbol = "LOBSTER/USDT"
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "open_timestamp": 600.0,
        "entry_mode": "WILD_SWING",
    }
    engine = FakeEngine()
    engine.account = Account(symbol, position)
    strategy = WildSwingStrategy()
    strategy.reset_symbol(symbol, 720_000.0)
    engine._wild_swing_strategy = strategy

    def confirmed_exit(_symbol, held_position, _frame, quote=None):
        state = held_position.get(STATE_KEY)
        if not isinstance(state, dict):
            state = {
                "version": 1,
                "identity": ["LONG", 600.0, 100.0, 1.0],
                "pending": {
                    "reason": "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL",
                    "details": {
                        "cause": "INTRADAY_PEAK_PULLBACK",
                        "peak_price": 110.0,
                    },
                },
            }
            held_position[STATE_KEY] = state
        details = state["pending"]["details"]
        reason = state["pending"]["reason"]
        return {
            "action": "CLOSE_LONG",
            "type": reason,
            "side": "LONG",
            "reason": reason,
            "bar_id": 780_000.0,
            "close": quote,
            "entry_mode": "WILD_SWING",
            "kc_exit": details,
        }

    strategy.evaluate_exit = confirmed_exit
    frame = _frame("LONG")
    frame["timestamp"] = [index * 60_000 for index in range(len(frame))]
    frame.attrs["timeframe_ms"] = 60_000

    asyncio.run(
        _process_wild_swing_symbol(
            engine, symbol, frame, 108.0, False, False,
        )
    )

    assert engine.account.close_calls
    assert STATE_KEY in engine.account.position_meta[symbol]
    assert engine.account.save_count == 1

    restored_position = json.loads(json.dumps(engine.account.positions[symbol]))
    restored_meta = json.loads(
        json.dumps(engine.account.position_meta[symbol])
    )
    engine.account.positions[symbol] = restored_position
    engine.account.position_meta[symbol] = restored_meta
    restored_strategy = WildSwingStrategy()
    restored_strategy.evaluate_exit = confirmed_exit
    engine._wild_swing_strategy = restored_strategy

    asyncio.run(
        _process_wild_swing_symbol(
            engine, symbol, frame, 107.5, False, False,
        )
    )

    assert len(engine.account.close_calls) == 2
    assert "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL" in engine.account.close_calls[1][2]
    assert symbol not in engine.account.positions


def test_runner_holds_through_live_ma5_pullback():
    class Account(FakeAccount):
        def __init__(self):
            super().__init__()
            self.close_calls = []
            self.last_closed_price = {}

        async def close_position(self, symbol, price, reason, **kwargs):
            self.close_calls.append((symbol, price, reason, kwargs))
            self.positions.pop(symbol, None)
            self.last_closed_price[symbol] = price
            return True

    class Engine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.account = Account()

    engine = Engine()
    symbol = "LOBSTER/USDT"
    engine.account.positions[symbol] = {
        "side": "LONG",
        "entry_price": 100.0,
        "open_timestamp": 600.0,
        "entry_mode": "WILD_SWING",
    }
    strategy = WildSwingStrategy()
    strategy.reset_symbol(symbol, 600_000.0)
    engine._wild_swing_strategy = strategy
    frame = _frame("LONG")
    frame["timestamp"] = [index * 60_000 for index in range(len(frame))]
    frame.loc[11, "is_closed"] = False
    frame.loc[11, "ma5"] = float(frame.loc[10, "ma5"]) - 0.1

    asyncio.run(_process_wild_swing_symbol(
        engine, symbol, frame, 99.0, False, False,
    ))

    assert engine.account.close_calls == []
    assert symbol in engine.account.positions
