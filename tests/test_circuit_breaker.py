"""Account-level 15% Live circuit breaker regression tests.

All exchange boundaries are mocked. State is injected via
BinanceTestnetAccount(state_file=<tmp_path>), never the production file.
"""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.testnet_account as ta  # noqa: F401
from core.testnet_account import BinanceTestnetAccount


def _run(coro):
    return asyncio.run(coro)


def _make_exchange():
    ex = MagicMock()
    ex.create_order = AsyncMock(return_value={"id": "raw-ok", "status": "FILLED"})
    ex.fapiPrivateV2GetAccount = AsyncMock(return_value={"totalMarginBalance": "10000"})
    ex.fapiPrivateV2GetPositionRisk = AsyncMock(return_value=[])
    ex.fapiPrivateV2GetBalance = AsyncMock(return_value=[])
    ex.fetch_open_orders = AsyncMock(return_value=[])
    ex.cancel_order = AsyncMock()
    ex.request = AsyncMock(return_value=[])
    return ex


@pytest.fixture
def state_paths(testnet_state_file):
    return testnet_state_file


@pytest.fixture
def live_env():
    with patch("core.config.PAPER_TRADING", False), patch("core.config.USE_TESTNET", False):
        yield


@pytest.fixture
def account(state_paths):
    acct = BinanceTestnetAccount(_make_exchange(), state_file=state_paths)
    acct.live_session_start_equity = "10000"
    acct.live_session_equity_available = True
    return acct


def _set_equity(acct, value):
    acct.exchange.fapiPrivateV2GetAccount.return_value = {"totalMarginBalance": value}


# 1-3. Exact Decimal boundary --------------------------------------------------
def test_1_8500_01_no_trigger(account, live_env):
    _set_equity(account, "8500.01")
    _run(account._check_live_circuit_breaker())
    assert account.circuit_breaker_latched is False
    account._raw_create_order.assert_not_called()


def test_2_8500_00_triggers(account, live_env):
    _set_equity(account, "8500.00")
    _run(account._check_live_circuit_breaker())
    assert account.circuit_breaker_latched is True
    assert Decimal(account.trigger_drawdown) == Decimal("-0.15")


def test_3_8499_99_triggers(account, live_env):
    _set_equity(account, "8499.99")
    _run(account._check_live_circuit_breaker())
    assert account.circuit_breaker_latched is True


def test_refresh_invokes_breaker_before_other_work(account, live_env):
    _set_equity(account, "8000")
    account.exchange.fapiPrivateV2GetBalance.side_effect = RuntimeError("stop after breaker")
    with patch("core.services.exits.staged_risk_service.refresh_staged_runtimes", AsyncMock()):
        with pytest.raises(RuntimeError):
            _run(account.refresh(force=True))
    assert account.circuit_breaker_latched is True


# 4-5. Latch persistence --------------------------------------------------------
def test_4_latch_survives_equity_recovery(account, live_env):
    _set_equity(account, "8000")
    _run(account._check_live_circuit_breaker())
    _set_equity(account, "9000")
    _run(account._check_live_circuit_breaker())
    assert account.circuit_breaker_latched is True
    with pytest.raises(ValueError, match="Circuit Breaker Latched"):
        _run(account.exchange.create_order("BTC/USDT", "market", "buy", 1.0))


def test_5_restart_still_latched(account, live_env, state_paths):
    _set_equity(account, "8000")
    _run(account._check_live_circuit_breaker())
    restarted = BinanceTestnetAccount(_make_exchange(), state_file=state_paths)
    assert restarted.circuit_breaker_latched is True
    assert restarted.live_session_start_equity == "10000"
    restarted.live_session_equity_available = True
    with pytest.raises(ValueError, match="Circuit Breaker Latched"):
        _run(restarted.exchange.create_order("BTC/USDT", "market", "buy", 1.0))
    restarted.exchange.fapiPrivateV2GetAccount.return_value = {"totalMarginBalance": "12000"}
    _run(restarted._check_live_circuit_breaker())
    assert restarted.circuit_breaker_latched is True


# 6-8. Fail-closed entry gate ----------------------------------------------------
def test_6_baseline_missing_blocks_live_entry(account, live_env):
    account.live_session_start_equity = None
    with pytest.raises(ValueError, match="Baseline Missing"):
        _run(account.exchange.create_order("BTC/USDT", "market", "buy", 1.0))


def test_7_equity_api_exception_blocks_live_entry(account, live_env):
    account.exchange.fapiPrivateV2GetAccount.side_effect = Exception("API down")
    _run(account._check_live_circuit_breaker())
    assert account.live_session_equity_available is False
    with pytest.raises(ValueError, match="Equity Unavailable"):
        _run(account.exchange.create_order("BTC/USDT", "market", "buy", 1.0))


@pytest.mark.parametrize("bad", ["bad", "NaN", "Infinity", "-1", "0", None, ""])
def test_8_malformed_equity_blocks_live_entry(account, live_env, bad):
    _set_equity(account, bad)
    _run(account._check_live_circuit_breaker())
    assert account.live_session_equity_available is False
    assert account.circuit_breaker_latched is False
    with pytest.raises(ValueError, match="Equity Unavailable"):
        _run(account.exchange.create_order("BTC/USDT", "market", "buy", 1.0))


# 9-12. Order classification -------------------------------------------------------
def test_9_opening_market_blocked_when_latched(account, live_env):
    account.circuit_breaker_latched = True
    with pytest.raises(ValueError, match="Circuit Breaker Latched"):
        _run(account.exchange.create_order("BTC/USDT", "market", "buy", 1.0))
    account._raw_create_order.assert_not_called()


def test_10_opening_limit_blocked_when_latched(account, live_env):
    account.circuit_breaker_latched = True
    with pytest.raises(ValueError, match="Circuit Breaker Latched"):
        _run(account.exchange.create_order("BTC/USDT", "limit", "sell", 1.0, 50000.0, {}))
    account._raw_create_order.assert_not_called()


def test_11_verified_reduce_only_allowed_when_latched(account, live_env):
    account.circuit_breaker_latched = True
    account.positions["BTC/USDT"] = {"side": "LONG", "qty": 1.0}
    result = _run(account.exchange.create_order(
        "BTC/USDT", "market", "sell", 1.0, None, {"reduceOnly": True}))
    assert result["id"] == "raw-ok"
    account._raw_create_order.assert_awaited_once()


@pytest.mark.parametrize("side,qty,positions", [
    ("sell", 2.0, {"BTC/USDT": {"side": "LONG", "qty": 1.0}}),   # qty > position
    ("buy", 0.5, {"BTC/USDT": {"side": "LONG", "qty": 1.0}}),    # same side adds exposure
    ("sell", 1.0, {}),                                           # no position at all
    ("sell", 0.0, {"BTC/USDT": {"side": "LONG", "qty": 1.0}}),   # zero qty
    ("sell", "nan", {"BTC/USDT": {"side": "LONG", "qty": 1.0}}), # malformed qty
])
def test_12_fake_reduce_only_blocked(account, live_env, side, qty, positions):
    account.circuit_breaker_latched = True
    account.positions = dict(positions)
    with pytest.raises(ValueError, match="Circuit Breaker Latched"):
        _run(account.exchange.create_order(
            "BTC/USDT", "market", side, qty, None, {"reduceOnly": True}))
    account._raw_create_order.assert_not_called()


# 13-16. Flatten sequence ------------------------------------------------------------
def _pos(symbol, amt):
    return {"symbol": symbol, "positionAmt": amt}


def test_13_close_api_failure_keeps_latch(account, live_env):
    account.exchange.fapiPrivateV2GetPositionRisk.return_value = [_pos("BTCUSDT", "0.5")]
    account._raw_create_order.side_effect = Exception("rejected")
    _set_equity(account, "8000")
    _run(account._check_live_circuit_breaker())
    assert account.circuit_breaker_latched is True
    assert account.circuit_breaker_position_flattened is False
    assert account.circuit_breaker_status.startswith("POSITION_FLATTENED_FALSE")
    with pytest.raises(ValueError, match="Circuit Breaker Latched"):
        _run(account.exchange.create_order("ETH/USDT", "market", "buy", 1.0))
    # Next refresh retries the close because the book is not verified flat.
    account._raw_create_order.side_effect = None
    _run(account._check_live_circuit_breaker())
    assert account._raw_create_order.await_count == 2


def test_14_partial_close_not_declared_flat(account, live_env):
    account.exchange.fapiPrivateV2GetPositionRisk.side_effect = [
        [_pos("BTCUSDT", "1.0")], [_pos("BTCUSDT", "0.4")]]
    _set_equity(account, "8000")
    _run(account._check_live_circuit_breaker())
    assert account.circuit_breaker_latched is True
    assert account.circuit_breaker_position_flattened is False


def test_15_protective_orders_not_cancelled_before_flat(account, live_env):
    account.exchange.fapiPrivateV2GetPositionRisk.return_value = [_pos("BTCUSDT", "1.0")]
    account.exchange.fetch_open_orders.return_value = [
        {"id": "entry-1", "symbol": "ETH/USDT", "type": "limit", "info": {"reduceOnly": False}},
        {"id": "tp-1", "symbol": "BTC/USDT", "type": "limit", "info": {"reduceOnly": True}},
        {"id": "sl-1", "symbol": "BTC/USDT", "type": "stop_market", "info": {"closePosition": "true"}},
    ]
    account._raw_create_order.side_effect = Exception("timeout")
    report = _run(account._run_circuit_breaker_flatten())
    cancelled = [c.args[0] for c in account.exchange.cancel_order.await_args_list]
    assert cancelled == ["entry-1"]          # only the pending ENTRY, by exact id
    account.exchange.request.assert_not_called()  # no algo SL/TP delete
    assert report["position_flattened"] is False
    assert report["orphan_protection_candidates"] == []


def test_15b_flat_book_reports_orphans_without_deleting(account, live_env):
    account.exchange.fapiPrivateV2GetPositionRisk.side_effect = [
        [_pos("BTCUSDT", "1.0")], [_pos("BTCUSDT", "0")]]
    report = _run(account._run_circuit_breaker_flatten())
    assert report["position_flattened"] is True
    assert report["orphan_protection_candidates"] == ["BTC/USDT"]
    account.exchange.request.assert_not_called()
    account.exchange.cancel_order.assert_not_called()


def test_16_multiple_positions_all_closed(account, live_env):
    account.exchange.fapiPrivateV2GetPositionRisk.side_effect = [
        [_pos("BTCUSDT", "1.0"), _pos("ETHUSDT", "-2.5"), _pos("SOLUSDT", "0")], []]
    report = _run(account._run_circuit_breaker_flatten())
    calls = [(c.args[0], c.args[2], c.args[3], c.args[5])
             for c in account._raw_create_order.await_args_list]
    assert calls == [
        ("BTC/USDT", "sell", 1.0, {"reduceOnly": True}),
        ("ETH/USDT", "buy", 2.5, {"reduceOnly": True}),
    ]
    assert report["position_flattened"] is True


def test_positions_unavailable_blocked_by_reconciliation(account, live_env):
    account.exchange.fapiPrivateV2GetPositionRisk.side_effect = Exception("down")
    report = _run(account._run_circuit_breaker_flatten())
    assert report["status"].startswith("BLOCKED_BY_RECONCILIATION")
    account._raw_create_order.assert_not_called()


def test_open_orders_unavailable_marks_pending_blocked(account, live_env):
    account.exchange.fapiPrivateV2GetPositionRisk.side_effect = [[_pos("BTCUSDT", "1")], []]
    account.exchange.fetch_open_orders.side_effect = Exception("down")
    report = _run(account._run_circuit_breaker_flatten())
    assert report["pending_entry_status"] == "BLOCKED_BY_RECONCILIATION"
    assert "PENDING_ENTRY_BLOCKED_BY_RECONCILIATION" in account.circuit_breaker_status
    account._raw_create_order.assert_awaited_once()  # close still attempted


# 17. Paper / Testnet: no live breaker exchange action ------------------------------
@pytest.mark.parametrize("paper,testnet", [(True, False), (False, True), (True, True)])
def test_17_non_live_no_breaker_action(account, paper, testnet):
    with patch("core.config.PAPER_TRADING", paper), patch("core.config.USE_TESTNET", testnet):
        _set_equity(account, "1")
        _run(account._check_live_circuit_breaker())
    assert account.circuit_breaker_latched is False
    account.exchange.fapiPrivateV2GetAccount.assert_not_called()
    account.exchange.fapiPrivateV2GetPositionRisk.assert_not_called()
    account._raw_create_order.assert_not_called()


# Owner reset hook (internal only) ---------------------------------------------------
def test_owner_reset_requires_authorization_and_flat(account, live_env):
    account.exchange.fapiPrivateV2GetPositionRisk.return_value = [_pos("BTCUSDT", "1")]
    account._raw_create_order.side_effect = Exception("rejected")
    _set_equity(account, "8000")
    _run(account._check_live_circuit_breaker())
    with pytest.raises(PermissionError):
        account._owner_reset_live_session_baseline("8000")
    with pytest.raises(PermissionError):
        account._owner_reset_live_session_baseline("8000", owner_authorized=True)
    assert account.circuit_breaker_latched is True
