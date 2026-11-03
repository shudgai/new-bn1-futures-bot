"""Actual-fill UTC-minute protection across strategy and account boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.services.exits.authorized_position_exit import (
    automated_position_exit_allowed, strategy_exit_bar_block_reason,
)
from core.services.math_execution_gate import validate_exit_at_execution
from core.gates.unified_pipeline_gates import ExitGate

NOW = 1793676000.0
SYMBOLS = ["1000PEPE/USDT", "龙虾/USDT", "CAP/USDT"]
REASONS = [
    "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT",
    "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT", "EXIT_BY_PULLBACK",
    "EXIT_BY_RATCHET_PROFIT_LOCK",
]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr("core.services.exits.authorized_position_exit.time.time", lambda: NOW + 30)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("reason", REASONS)
@pytest.mark.parametrize("milliseconds", [False, True])
def test_actual_entry_minute_blocks_every_ordinary_exit(side, symbol, reason, milliseconds):
    pos = {
        "side": side, "entry_price": 100, "qty": 1,
        "open_timestamp": (NOW + 1) * (1000 if milliseconds else 1),
    }
    assert not automated_position_exit_allowed(symbol, pos, {}, reason, 90)
    assert validate_exit_at_execution(
        SimpleNamespace(position_meta={}), symbol, pos, 90, reason,
    )[1] == "VETO_HOLD_ENTRY_CANDLE"
    allowed, code, _ = ExitGate.evaluate(pos, {"current_price": 100})
    assert not allowed and code == "VETO_HOLD_ENTRY_CANDLE"
    assert validate_exit_at_execution(
        SimpleNamespace(position_meta={}), symbol, pos, 90, "手動平倉",
    )[0]


@pytest.mark.parametrize("delta,blocked", [(59.999, True), (60.0, False), (60.001, False)])
def test_exact_next_utc_minute_resumes_policy(delta, blocked, monkeypatch):
    monkeypatch.setattr("core.services.exits.authorized_position_exit.time.time", lambda: NOW + delta)
    assert (strategy_exit_bar_block_reason({"open_timestamp": NOW + 1}) is not None) is blocked


@pytest.mark.parametrize("opened", [None, 0, -1, float("nan"), float("inf"), NOW + 40])
def test_invalid_open_time_fail_closed(opened):
    assert strategy_exit_bar_block_reason({"open_timestamp": opened}) == "VETO_EXIT_OPEN_TIME_INVALID"


def test_persisted_actual_time_not_signal_time():
    assert strategy_exit_bar_block_reason(
        {"open_timestamp": NOW + 1, "signal_timestamp": NOW - 600},
        {"open_timestamp": NOW - 600},
    ) == "VETO_HOLD_ENTRY_CANDLE"
    assert strategy_exit_bar_block_reason({}, {"open_timestamp": NOW + 1}) == "VETO_HOLD_ENTRY_CANDLE"
    assert strategy_exit_bar_block_reason({"open_timestamp": NOW - 60}) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_verified_disaster_allowed_even_on_entry_bar_but_never_pullback(side, monkeypatch):
    pos = {"side": side, "entry_price": 100, "qty": 1, "open_timestamp": NOW + 1}
    monkeypatch.setattr(
        ExitGate, "_evaluate_core",
        staticmethod(lambda *_a, **_k: (True, "EXIT_BY_EXTREME_WATERFALL", {"verified": True})),
    )
    assert ExitGate.evaluate(pos, {"current_price": 90})[1] == "EXIT_BY_EXTREME_WATERFALL"
    monkeypatch.setattr("core.services.exits.authorized_position_exit.time.time", lambda: NOW + 60)
    assert ExitGate.evaluate(pos, {"current_price": 90})[1] == "EXIT_BY_EXTREME_WATERFALL"
    monkeypatch.setattr(
        "core.services.math_execution_gate.validate_exit_math",
        lambda *_a, **_k: (True, "EMERGENCY_EXIT_ABNORMAL_CANDLE_CONFIRMED", {}),
    )
    assert automated_position_exit_allowed("CAP/USDT", pos, {}, "EXIT_BY_EXTREME_WATERFALL", 90)
    assert not automated_position_exit_allowed("CAP/USDT", pos, {}, "OPPOSITE_KC_BAND_BREACH", 90)
    assert not automated_position_exit_allowed("CAP/USDT", pos, {}, "EXIT_BY_PULLBACK", 90)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reason", [
    "EXIT_BY_EXTREME_WATERFALL", "EXIT_BY_TWO_CLOSED_ADVERSE_ABNORMAL",
    "OPPOSITE_KC_BAND_BREACH", "CIRCUIT_BREAKER_HARD_SL",
])
def test_disaster_label_without_math_proof_cannot_bypass_entry_bar(side, reason):
    pos = {"side": side, "entry_price": 100, "qty": 1, "open_timestamp": NOW + 1}
    assert not automated_position_exit_allowed("CAP/USDT", pos, {}, reason, 100)
    assert not validate_exit_at_execution(
        SimpleNamespace(position_meta={}), "CAP/USDT", pos, 100, reason,
    )[0]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("account_type", ["paper", "testnet"])
def test_real_account_blocks_entry_bar_waterfall_under_new_meme_policy(
    side, account_type, monkeypatch, tmp_path,
):
    import pandas as pd
    from core.paper_account import PaperAccount
    from core.testnet_account import BinanceTestnetAccount
    from test_testnet_account import FakeTestnetExchange
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    if account_type == "paper":
        account = PaperAccount()
    else:
        exchange = FakeTestnetExchange()
        account = BinanceTestnetAccount(exchange, state_file=str(tmp_path / "testnet.json"))
        account.refresh = AsyncMock()
    pos = {
        "symbol": "CAP/USDT", "side": side, "entry_price": 100, "qty": 1,
        "margin": 20, "leverage": 5, "open_timestamp": NOW + 1,
    }
    frame = pd.DataFrame([
        {"timestamp": (NOW - 60) * 1000, "open": 100, "close": 100, "atr": 1,
         "kc_middle": 100, "high": 101, "low": 99, "is_closed": True},
        {"timestamp": NOW * 1000, "open": 100, "close": 100, "atr": 1,
         "kc_middle": 100, "high": 103, "low": 97, "is_closed": False},
    ])
    account.positions["CAP/USDT"] = pos
    account.position_meta["CAP/USDT"] = dict(pos)
    account._channel_exit_frames = {"CAP/USDT": frame}
    quote = 98 if side == "LONG" else 102
    assert not asyncio.run(account.close_position("CAP/USDT", quote, "EXIT_BY_EXTREME_WATERFALL"))
    assert "CAP/USDT" in account.positions
    assert len(account.trades) == 0


def test_restart_does_not_reset_actual_entry_minute(tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    account = PaperAccount()
    account.positions["CAP/USDT"] = {
        "symbol": "CAP/USDT", "side": "LONG", "entry_price": 100, "qty": 1,
        "margin": 20, "leverage": 5, "open_timestamp": NOW + 1,
    }
    account.save_state()
    restored = PaperAccount()
    assert strategy_exit_bar_block_reason(restored.positions["CAP/USDT"]) == "VETO_HOLD_ENTRY_CANDLE"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("account_type", ["paper", "testnet"])
@pytest.mark.parametrize("manual_flag", [False, True])
def test_full_and_partial_account_close_never_submits_in_entry_minute(
    side, symbol, account_type, manual_flag, tmp_path, monkeypatch,
):
    from core.paper_account import PaperAccount
    from core.testnet_account import BinanceTestnetAccount
    from test_testnet_account import FakeTestnetExchange

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    if account_type == "paper":
        account = PaperAccount()
    else:
        exchange = FakeTestnetExchange()
        submit = AsyncMock(side_effect=AssertionError("entry-bar close submitted"))
        exchange.create_order = submit
        account = BinanceTestnetAccount(exchange, state_file=str(tmp_path / "exchange.json"))
        account.refresh = AsyncMock()
    account.positions[symbol] = {
        "symbol": symbol, "side": side, "entry_price": 100, "qty": 1,
        "margin": 20, "leverage": 5, "open_timestamp": NOW + 1,
    }
    account.position_meta[symbol] = dict(account.positions[symbol])
    async def run():
        assert not await account.close_position(
            symbol, 100, "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", is_manual=manual_flag,
        )
        assert not await account.partial_close_position(
            symbol, 100, "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", fraction=0.5, is_manual=manual_flag,
        )
    asyncio.run(run())
    assert symbol in account.positions
    assert not account.trades
    if account_type == "testnet":
        submit.assert_not_awaited()
