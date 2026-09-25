"""Five red-eye scenarios. Default SUT is a REFERENCE, not production.

Set RED_EYE_FACTORY=package.module:factory to exercise a production adapter.
Factory signature: (position, exchange, old_breakeven, old_drawdown, risk_params).
"""

import importlib
import json
import os
from unittest.mock import Mock

import pytest

from red_eye_support import MockExchange, PositionState, ReferenceStagedStrategy, RISK_PARAMS


@pytest.fixture
def build():
    target = os.getenv("RED_EYE_FACTORY")
    if os.getenv("RED_EYE_REQUIRE_PRODUCTION") == "1" and not target:
        pytest.fail("Production adapter required: set RED_EYE_FACTORY; reference cannot approve deployment")
    factory = ReferenceStagedStrategy
    if target:
        module, name = target.split(":", 1)
        factory = getattr(importlib.import_module(module), name)

    def create(position, exchange, option="B"):
        old_be = Mock(side_effect=exchange.old_logic)
        old_dd = Mock(side_effect=exchange.old_logic)
        sut = factory(position, exchange, old_be, old_dd, RISK_PARAMS[option])
        return sut, old_be, old_dd

    print(f"SUT={target or 'REFERENCE ONLY — not a deployment gate'}")
    return create


def assert_legacy_silent(exchange, old_be, old_dd):
    old_be.assert_not_called()
    old_dd.assert_not_called()
    assert exchange.call_count_for_old_logic == 0


def placed(exchange, kind=None):
    return [event for event in exchange.events
            if event[0] == "place" and (kind is None or event[2] == kind)]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_flash_gap(build, side):
    # Arrange: exact LONG 100/99 -> 103; mirrored SHORT 100/101 -> 97.
    sign = 1 if side == "LONG" else -1
    p = PositionState(side=side, initial_stop=100-sign, stop_price=100-sign)
    ex = MockExchange()
    sut, old_be, old_dd = build(p, ex)

    # Act: pre-entry High=150/Low=50 must not contaminate either extreme.
    sut.tick(100+3*sign, 1, 30, 1, historical_high=150, historical_low=50)
    assert p.stage == 3
    assert p.stop_price == 100+2*sign
    assert p.highest_since_entry == (103 if sign == 1 else 100)
    assert p.lowest_since_entry == (100 if sign == 1 else 97)
    assert len(placed(ex, "PARTIAL_TP")) == 1
    assert placed(ex, "PARTIAL_TP")[0][3:] == (5.0, True)

    # ATR expansion and lower R must never loosen the stop or downgrade stage.
    sut.tick(100+2*sign, 8, 20, 1)
    assert p.stage == 3
    assert p.stop_price == 100+2*sign
    # Restart using serialized state, not the original in-memory object.
    restored = PositionState(**json.loads(json.dumps(p.snapshot())))
    resumed, be2, dd2 = build(restored, ex)
    resumed.tick(100+2*sign, 8, 20, 1)
    assert restored.stage == 3
    assert restored.stop_price == 100+2*sign
    assert len(placed(ex, "PARTIAL_TP")) == 1
    assert_legacy_silent(ex, old_be, old_dd)
    assert_legacy_silent(ex, be2, dd2)
    print(f"FLASH_GAP {side}: stage=3, one 50% TP, ratchet survives restart")


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_fake_breakout(build, side):
    # Arrange: STOP execution report is deliberately absent in this scenario.
    sign = 1 if side == "LONG" else -1
    p = PositionState(side=side, initial_stop=100-sign, stage=2,
                      stop_price=100+1.5*sign, peak_net_pnl=10,
                      highest_since_entry=102, lowest_since_entry=98, last_bar=0)
    ex = MockExchange()
    sut, old_be, old_dd = build(p, ex)
    # Act: 1R is deliberately independent from injected net PNL.
    for bar in range(1, 5):
        sut.tick(100+sign, 1, 7.5, bar)
        sut.tick(100+sign, 1, 7.5, bar)  # duplicate quote is NOT another bar
        assert p.stage == 2
        assert p.stop_price == 100+1.5*sign
        assert p.bars_without_new_extreme == bar
        assert not placed(ex)
    sut.tick(100+sign, 1, 7.5, 5)
    # Assert
    assert p.stage == 2
    assert p.stop_price == 100+1.5*sign
    assert p.status == "CLOSED"
    assert len(placed(ex, "MARKET_EXIT")) == 1
    assert "CHOPPY_EXIT" in sut.logs
    assert_legacy_silent(ex, old_be, old_dd)
    print(f"FAKE_BREAKOUT {side}: five distinct bars, no downgrade, CHOPPY_EXIT")


@pytest.mark.parametrize("fault", ["timeout_after_fill", "unknown_after_fill"])
def test_order_black_hole(build, fault):
    # Arrange: exchange fills STOP, client loses its acknowledgement.
    p, ex = PositionState(), MockExchange()
    ex.place_fault = fault  # 故障注入：成交後模擬網路斷線／UNKNOWN。
    sut, old_be, old_dd = build(p, ex)
    # Act
    sut.submit_stop()
    assert p.status == "RECONCILE"
    assert ex.qty == 0
    assert p.pending_order_id is not None
    p = PositionState(**json.loads(json.dumps(p.snapshot())))
    resumed, be2, dd2 = build(p, ex)
    ex.position_timeout = True  # 故障注入：重連後第一次查詢也逾時。
    resumed.reconcile()
    resumed.tick(103, 1, 30, 1)
    resumed.submit_stop()
    assert p.status == "RECONCILE"
    assert len(placed(ex)) == 1
    ex.position_timeout = False
    resumed.reconcile()
    resumed.reconcile()
    resumed.tick(103, 1, 30, 2)
    # Assert: no duplicate STOP, TP, close or restoration order.
    assert p.status == "CLOSED"
    assert p.qty == 0
    assert len(placed(ex)) == 1
    assert any("RECONCILED_FLAT" in line for line in resumed.logs)
    assert_legacy_silent(ex, old_be, old_dd)
    assert_legacy_silent(ex, be2, dd2)
    print(f"ORDER_BLACK_HOLE {fault}: exchange Flat -> CLOSED; no recovery order")


@pytest.mark.parametrize("fault", [None, "timeout", "fill_then_timeout"])
def test_micro_chop(build, fault):
    # Arrange: TP is in flight before the exact five-bar/25% boundary.
    p = PositionState(stage=2, stop_price=101.5, peak_net_pnl=10,
                      highest_since_entry=102, bars_without_new_extreme=4, last_bar=4)
    ex = MockExchange()
    ex.place_order("existing-tp", "PARTIAL_TP", 5)
    p.tp_order_id = "existing-tp"
    sut, old_be, old_dd = build(p, ex)
    sut.tick(101, 1, 7.5001, 5)
    assert not placed(ex, "MARKET_EXIT")  # just below 25% does not trigger
    ex.cancel_fault = fault  # 故障注入：撤單逾時，含 TP 搶先成交競態。
    # Act: same bar, exact 25% giveback.
    sut.tick(101, 1, 7.5, 5)
    if fault:
        assert p.status == "RECONCILE"
        assert not placed(ex, "MARKET_EXIT")
        before = len(placed(ex))
        sut.tick(101, 1, 7.5, 5)
        sut.submit_stop()
        ex.ambiguous_orders = True
        sut.reconcile()
        assert p.status == "RECONCILE"
        assert len(placed(ex)) == before
        ex.ambiguous_orders = False
        if fault == "timeout":
            sut.reconcile()
            assert p.status == "RECONCILE"  # TP still OPEN is not safe
            ex.cancel_order("existing-tp")  # delayed authoritative cancellation
        sut.reconcile()
        sut.tick(101, 1, 7.5, 5)
    # Assert: only close remaining quantity, after TP is terminal.
    assert p.status == "CLOSED"
    assert ex.qty == 0
    closes = placed(ex, "MARKET_EXIT")
    assert len(closes) == 1
    assert closes[0][3:] == (5 if fault == "fill_then_timeout" else 10, True)
    assert ex.orders["existing-tp"]["status"] in {"CANCELED", "FILLED"}
    assert ex.events.index(("cancel", "existing-tp")) < ex.events.index(closes[0])
    assert "CHOPPY_EXIT" in sut.logs
    assert p.stage == 2 and p.stop_price == 101.5
    assert_legacy_silent(ex, old_be, old_dd)
    print(f"MICRO_CHOP {fault}: TP resolved before one reduce-only full exit")


def test_extreme_deviation(build):
    # Arrange: Price=103.1 > MA7(100) + 3*ATR(1), no position.
    p, ex = PositionState(qty=0, status="FLAT"), MockExchange(qty=0)
    sut, old_be, old_dd = build(p, ex)
    # Act
    result = sut.evaluate_entry(price=103.1, ma7=100, atr=1)
    # Assert
    assert result == "ENTRY_BLOCKED"
    assert any("Deviation > 2.5 ATR" in line for line in sut.logs)
    assert not placed(ex)
    assert p.status == "FLAT" and ex.qty == 0
    assert_legacy_silent(ex, old_be, old_dd)
    print("EXTREME_DEVIATION: ENTRY_BLOCKED; no exchange order")


@pytest.mark.parametrize("option", ["A", "B"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_stage_upgrade_and_trailing(build, option, side):
    # ASSUMPTION: Using Option A/B parameters, selected explicitly by pytest.
    # Arrange
    params = RISK_PARAMS[option]
    sign = 1 if side == "LONG" else -1
    p = PositionState(side=side, initial_stop=100-sign, stop_price=100-sign)
    ex = MockExchange()
    sut, old_be, old_dd = build(p, ex, option)
    trigger = params["partial_tp_r"]
    # Act / Assert: below threshold, exact boundary, then 3R.
    sut.tick(100+sign*(trigger-0.01), 0.5, 2.5, 1)
    assert p.stage == 1
    assert p.stop_price == p.initial_stop
    assert not placed(ex)
    price = 100+sign*trigger
    sut.tick(price, 0.5, 3.0, 1)
    assert p.stage == 2
    expected = price-sign*params["atr_multipliers"][2]*0.5
    assert p.stop_price == pytest.approx(expected)
    assert len(placed(ex, "PARTIAL_TP")) == 1
    assert placed(ex, "PARTIAL_TP")[0][3:] == (5, True)
    assert p.partial_tp_sent and not p.is_partial_tp_executed
    sut.tick(100+3*sign, 0.5, 10, 1)
    assert p.stage == 3
    assert p.stop_price == pytest.approx(100+2.5*sign)
    assert len(placed(ex, "PARTIAL_TP")) == 1
    assert_legacy_silent(ex, old_be, old_dd)
    print(f"UPGRADE Option {option} {side}: thresholds and ATR ratchet verified")


@pytest.mark.parametrize("option", ["A", "B"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_stage_monotonicity(build, option, side):
    # ASSUMPTION: Using Option A/B parameters, selected explicitly by pytest.
    # Arrange
    sign = 1 if side == "LONG" else -1
    p = PositionState(side=side, initial_stop=100-sign, stop_price=100-sign)
    ex = MockExchange()
    sut, old_be, old_dd = build(p, ex, option)
    # Act
    sut.tick(100+2*sign, 0.5, 10, 1)
    before = p.stop_price
    assert p.stage == 2
    for price, atr in [(100+0.5*sign, 4), (100+0.25*sign, 8)]:
        sut.tick(price, atr, 7.5, 1)
        # Assert: lower R and expanding ATR cannot loosen protection.
        assert p.stage == 2
        assert p.stop_price == before
        assert p.status == "OPEN"
    assert len(placed(ex, "PARTIAL_TP")) == 1
    assert_legacy_silent(ex, old_be, old_dd)
    print(f"MONOTONICITY Option {option} {side}: stage and stop preserved")


@pytest.mark.parametrize("option", ["A", "B"])
def test_legacy_suppression(build, option):
    # ASSUMPTION: Using Option A/B parameters, selected explicitly by pytest.
    # Arrange: R and injected net PNL are independent inputs.
    p, ex = PositionState(), MockExchange()
    sut, old_be, old_dd = build(p, ex, option)
    # Act: old activation, old drawdown, even old 0.5U floor.
    for price, net in [(100.5, 2.5), (100.6, 3.0), (100.4, 2.0), (100.1, 0.5)]:
        sut.tick(price, 1, net, 1)
        # Assert: Stage 1 keeps initial stop; no legacy order.
        assert p.stage == 1
        assert p.stop_price == p.initial_stop
        assert p.status == "OPEN"
        assert not placed(ex)
        assert_legacy_silent(ex, old_be, old_dd)
    print(f"LEGACY_SUPPRESSION Option {option}: old hooks silent")
