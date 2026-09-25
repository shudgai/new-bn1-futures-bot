"""Adapter fidelity checks: green here does NOT mean staged policy passed."""

import math
import socket
import sys
from unittest.mock import Mock

import pytest

from my_package import red_eye_adapter as bridge
from red_eye_support import MockExchange, PositionState, RISK_PARAMS, ReferenceStagedStrategy


@pytest.fixture(autouse=True)
def prohibit_external_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Red-eye tests attempted external networking")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def price_for_net(net):
    # Fixture construction only: invert fee/slippage valuation, not risk logic.
    return (100 * 1.0005 + net / 10) / (0.9995 * 0.9995)


def test_adapter_calls_actual_runner_and_inline_legacy_branch(monkeypatch):
    # Arrange: make any use of the reference strategy fail immediately.
    monkeypatch.setattr(ReferenceStagedStrategy, "tick", Mock(side_effect=AssertionError("reference called")))
    actual = bridge.symbol_runner.process_single_symbol_runner
    from unittest.mock import AsyncMock
    runner_spy = AsyncMock(wraps=actual)
    monkeypatch.setattr(bridge.symbol_runner, "process_single_symbol_runner", runner_spy)
    p, ex = PositionState(), MockExchange()
    be, dd = Mock(), Mock()
    sut = bridge.create_strategy(p, ex, be, dd, RISK_PARAMS["B"])
    previous_trace = sys.gettrace()
    # Act: exact 2.5U boundary approached from above to avoid binary rounding.
    sut.tick_native(math.nextafter(price_for_net(2.5), math.inf), 1, 1)
    # Assert: real valuation, real old branch, no reference stage logic.
    runner_spy.assert_awaited_once()
    assert sut.native_net == pytest.approx(2.5, abs=1e-10)
    assert sut.native_net >= 2.5
    be.assert_called_once_with()
    dd.assert_not_called()
    assert sut.observed_legacy == ["legacy_breakeven"]
    assert p.stage == 1 and p.status == "OPEN"
    assert not ex.orders
    assert sys.gettrace() is previous_trace


def test_real_drawdown_closes_without_waiting_for_tp_cancel():
    # Arrange: this documents a CURRENT CORE gap, not desired staged behavior.
    p = PositionState(stage=2, peak_net_pnl=10, bars_without_new_extreme=0)
    ex = MockExchange()
    ex.place_order("inflight-tp", "PARTIAL_TP", 5)
    p.tp_order_id = "inflight-tp"
    sut = bridge.create_strategy(p, ex, Mock(), Mock(), RISK_PARAMS["B"])
    # Act: net 7.5U with native valuation; just under boundary for float safety.
    sut.tick_native(math.nextafter(price_for_net(7.5), -math.inf), 1, 1)
    # Assert: core chooses old drawdown and calls account.close_position.
    assert sut.native_net == pytest.approx(7.5, abs=1e-10)
    assert p.status == "CLOSED"
    assert p.bars_without_new_extreme == 0
    assert any("EXIT_PEAK_DRAWDOWN" in reason for reason in sut.order_reasons)
    assert ex.orders["inflight-tp"]["status"] == "OPEN"
    assert not any(event[0] == "cancel" for event in ex.events)
    assert sum(event[0] == "place" and event[2] == "MARKET_EXIT" for event in ex.events) == 1


def test_actual_legacy_observer_failure_is_not_swallowed():
    p, ex = PositionState(), MockExchange()
    sut = bridge.create_strategy(p, ex, ex.old_logic, ex.old_logic, RISK_PARAMS["B"])
    previous_trace = sys.gettrace()
    with pytest.raises(AssertionError, match="PRODUCTION_LEGACY_NOT_SILENT"):
        sut.tick_native(103, 1, 1)
    assert ex.call_count_for_old_logic == 2
    assert sys.gettrace() is previous_trace


def test_independent_pnl_is_not_silently_injected_into_core():
    sut = bridge.create_strategy(PositionState(), MockExchange(), Mock(), Mock(), RISK_PARAMS["B"])
    with pytest.raises(bridge.ProductionValuationMismatch, match="core was NOT overwritten"):
        sut.tick(100.2, 1, 0.5, 1)
    assert sut.native_net != pytest.approx(0.5)
    assert sut.production_calls == 1


def test_missing_reconcile_is_explicit_and_sends_nothing():
    p, ex = PositionState(status="RECONCILE"), MockExchange()
    sut = bridge.create_strategy(p, ex, Mock(), Mock(), RISK_PARAMS["B"])
    with pytest.raises(bridge.ProductionCapabilityMissing, match="MISSING_STAGED_RECONCILE"):
        sut.reconcile()
    assert not ex.events
    assert p.status == "RECONCILE"


def test_entry_reason_is_preserved_from_actual_strategy():
    sut = bridge.create_strategy(PositionState(qty=0, status="FLAT"), MockExchange(qty=0),
                                 Mock(), Mock(), RISK_PARAMS["B"])
    assert sut.evaluate_entry(103.1, 100, 1) == "ENTRY_BLOCKED"
    assert sut.logs[-1] == "🛑 BLOCKED_PANIC_LONG (Bias > 2.5 ATR)"
    assert not sut.exchange.orders
