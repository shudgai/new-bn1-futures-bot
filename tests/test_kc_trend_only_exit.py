import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.gates.unified_pipeline_gates import ExitGate
from core.services.exits.authorized_position_exit import automated_position_exit_allowed
from core.services.exits.trend_lock import KC_WAVE_EXIT_REASON
from core.services.math_execution_gate import validate_exit_at_execution
from test_kc_wave_exit import position, wave_frame


SYMBOLS = ["1000PEPE/USDT", "龙虾/USDT", "CAP/USDT"]
ORDINARY_EXITS = [
    "EXIT_BY_RATCHET_PROFIT_LOCK", "EXIT_BY_SCHEME1_PROFIT_LOCK_MA3_MA15",
    "EXIT_BY_STAGED_PROFIT_LOCK_TIER1", "EXIT_BY_PULLBACK",
    "EARLY_PIVOT_PEAK_EXIT", "EARLY_PIVOT_VALLEY_EXIT",
    "觸發移動止利 (Trailing Take-Profit)", "峰值回吐平倉",
]


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reason", ORDINARY_EXITS)
def test_armed_profit_or_old_pending_exit_cannot_close_unchanged_trend(
    monkeypatch, symbol, side, reason,
):
    frame = wave_frame(side, reversed_direction=False)
    pos = position(side, frame)
    pos.update(symbol=symbol, pending_exit=reason, breakeven_locked=True,
               breakeven_price=101 if side == "LONG" else 99)
    verified = lambda *args, **kwargs: {"lock_type": "VERIFIED_TEST_LOCK"}
    monkeypatch.setattr(
        "core.gates.holding_protection_gate._verified_profit_lock_details", verified,
    )
    account = SimpleNamespace(_channel_exit_frames={symbol: frame}, position_meta={})
    assert not automated_position_exit_allowed(
        symbol, pos, {"pending_exit": reason}, reason, 100.2, frame=frame,
    )
    assert not validate_exit_at_execution(account, symbol, pos, 100.2, reason)[0]
    monkeypatch.setattr(ExitGate, "_evaluate_core", staticmethod(
        lambda *args, **kwargs: (True, reason, {"lock_armed": True}),
    ))
    allowed, _, evidence = ExitGate.evaluate(
        pos, {"raw_df": frame, "current_price": 100.2},
    )
    assert not allowed
    assert evidence["exit_policy"] == "PROFIT_CONFIRMED_TRUE_PIVOT_ONLY_V1"


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_plain_kc_reversal_and_profit_lock_cannot_replace_true_pivot(monkeypatch, symbol, side):
    frame = wave_frame(side)
    pos = position(side, frame)
    pos["symbol"] = symbol
    monkeypatch.setattr(ExitGate, "_evaluate_core", staticmethod(
        lambda *args, **kwargs: (True, "EXIT_BY_RATCHET_PROFIT_LOCK", {"lock_armed": True}),
    ))
    allowed, reason, _ = ExitGate.evaluate(
        pos, {"raw_df": frame, "current_price": 100.2},
    )
    assert not allowed and reason == "VETO_HOLD"
    account = SimpleNamespace(_channel_exit_frames={symbol: frame}, position_meta={})
    assert not validate_exit_at_execution(account, symbol, pos, 100.2, reason)[0]


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pipeline_suppresses_scheme1_profit_close_until_kc_reversal(
    monkeypatch, symbol, side,
):
    from core.services.data_pipeline import DataPipeline
    from core.services.execution_manager import ExecutionManager

    frame = wave_frame(side, reversed_direction=False)
    pos = position(side, frame)
    pos["symbol"] = symbol
    snapshot = DataPipeline.calculate_snapshot(symbol, frame, current_price=100.2)
    assert snapshot is not None
    snapshot.raw_df = frame
    account = SimpleNamespace(
        positions={symbol: pos}, position_meta={}, entry_filter_last={},
        save_state=lambda **kwargs: None,
    )
    engine = SimpleNamespace(
        account=account, tickers={symbol: 100.2},
        fetch_klines=AsyncMock(return_value=frame),
    )
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(
        lambda *args, **kwargs: snapshot,
    ))
    monkeypatch.setattr(
        "core.services.exits.staged_profit_lock.check_exit_conditions",
        lambda *args, **kwargs: "CLOSE_" + side,
    )
    close = AsyncMock(return_value=True)
    monkeypatch.setattr(ExecutionManager, "close_position", close)
    result = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol, engine, klines_df=frame, quote=100.2, frame_15m=frame,
    ))
    assert result["status"] == "HOLD", result
    assert result["reason"] == "HOLD_UNTIL_PROFIT_CONFIRMED_TRUE_PIVOT"
    close.assert_not_awaited()


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_manual_close_remains_independent_of_kc_direction(side):
    frame = wave_frame(side, reversed_direction=False)
    pos = position(side, frame)
    assert automated_position_exit_allowed(
        "CAP/USDT", pos, {}, "手動平倉", 100.2, is_manual=True, frame=frame,
    )
    assert validate_exit_at_execution(
        SimpleNamespace(position_meta={}), "CAP/USDT", pos, 100.2, "手動平倉",
    )[0]


@pytest.mark.parametrize("account_type", ["paper", "testnet"])
@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_real_account_rejects_profit_exit_without_submitting(
    tmp_path, monkeypatch, account_type, symbol, side,
):
    import core.paper_account as pm
    import core.testnet_account as tm
    from test_testnet_account import FakeTestnetExchange

    exchange = None
    if account_type == "paper":
        monkeypatch.setattr(pm, "STATE_FILE", str(tmp_path / "hold-paper.json"))
        account = pm.PaperAccount()
    else:
        monkeypatch.setattr(tm, "notify_email", lambda *args, **kwargs: None)
        exchange = FakeTestnetExchange()
        account = tm.BinanceTestnetAccount(exchange, state_file=str(tmp_path / "hold-testnet.json"))
        account.refresh = AsyncMock()
    frame = wave_frame(side, reversed_direction=False)
    pos = position(side, frame)
    pos.update(symbol=symbol, breakeven_locked=True,
               breakeven_price=101 if side == "LONG" else 99)
    account.positions[symbol] = pos
    account.position_meta[symbol] = {"profit_lock_usdt_armed": True}
    account._channel_exit_frames = {symbol: frame}
    monkeypatch.setattr(
        "core.gates.holding_protection_gate._verified_profit_lock_details",
        lambda *args, **kwargs: {"lock_type": "VERIFIED_TEST_LOCK"},
    )
    closed = asyncio.run(account.close_position(
        symbol, 100.2, "EXIT_BY_RATCHET_PROFIT_LOCK", is_manual=True,
    ))
    assert not closed
    assert account.positions[symbol] is pos
    assert not account.trades
    if exchange is not None:
        assert not exchange.orders


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_numeric_hard_stop_cannot_override_true_pivot_hold(symbol, side):
    frame = wave_frame(side, reversed_direction=False)
    pos = position(side, frame)
    pos["symbol"] = symbol
    price = 97 if side == "LONG" else 103
    account = SimpleNamespace(_channel_exit_frames={symbol: frame}, position_meta={})
    assert not automated_position_exit_allowed(
        symbol, pos, {}, "CIRCUIT_BREAKER_HARD_SL", price, frame=frame,
    )
    allowed, reason, evidence = validate_exit_at_execution(
        account, symbol, pos, price, "CIRCUIT_BREAKER_HARD_SL",
    )
    assert not allowed and reason == "BLOCKED_TREND_LOCK_OR_UNCONFIRMED_EMERGENCY"
    assert evidence["exit_type"] == "BLOCKED"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_profit_floor_cannot_be_relabelled_as_emergency(side):
    frame = wave_frame(side, reversed_direction=False)
    pos = position(side, frame)
    pos.update(sl=101 if side == "LONG" else 99, breakeven_locked=True)
    assert not automated_position_exit_allowed(
        "CAP/USDT", pos, {}, "CIRCUIT_BREAKER_HARD_SL", 100.2, frame=frame,
    )
