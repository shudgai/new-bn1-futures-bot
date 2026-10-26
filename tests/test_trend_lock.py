import asyncio

import pandas as pd
import pytest

from core.services.exits.trend_lock import (
    evaluate_trend_lock_exit,
    evaluate_trend_cycle_entry,
    claim_trend_cycle,
)


def frame_for(side, *, prior_close=100.0, current_close=100.0,
              ma3=(100.0, 100.0), ma15=(100.0, 100.0),
              middle=(100.0, 100.0), upper=(102.0, 102.0),
              lower=(98.0, 98.0), prior_open=100.0,
              current_open=100.0, prior_high=101.0,
              prior_low=99.0, current_high=101.0,
              current_low=99.0, current_closed=True):
    frame = pd.DataFrame([
        {"timestamp": 60_000, "open": prior_open, "close": prior_close,
         "high": prior_high, "low": prior_low, "ma3": ma3[0],
         "ma15": ma15[0], "kc_middle": middle[0],
         "kc_upper": upper[0], "kc_lower": lower[0], "is_closed": True},
        {"timestamp": 120_000, "open": current_open,
         "close": current_close, "high": current_high, "low": current_low,
         "ma3": ma3[1], "ma15": ma15[1], "kc_middle": middle[1],
         "kc_upper": upper[1], "kc_lower": lower[1],
         "is_closed": current_closed},
    ])
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["snapshot_ms"] = 180_000
    return frame


@pytest.mark.parametrize("side,opening,close", [
    ("LONG", 100.0, 99.7), ("SHORT", 100.0, 100.3),
])
def test_small_countertrend_pullback_does_not_exit(side, opening, close):
    middle = (99.0, 99.0) if side == "LONG" else (101.0, 101.0)
    frame = frame_for(
        side, current_open=opening, current_close=close, middle=middle,
        current_high=max(opening, close) + 0.05,
        current_low=min(opening, close) - 0.05,
    )
    position = {"side": side, "open_timestamp": 0.0, "entry_price": 100.0}
    assert evaluate_trend_lock_exit(position, frame) is None


@pytest.mark.parametrize("side,opens,closes,middle", [
    ("LONG", [101.0, 100.8, 100.6], [100.8, 100.6, 100.4],
     [99.0, 99.2, 99.4]),
    ("SHORT", [99.0, 99.2, 99.4], [99.2, 99.4, 99.6],
     [101.0, 100.8, 100.6]),
])
def test_single_and_consecutive_counter_color_bars_do_not_exit(
    side, opens, closes, middle,
):
    rows = []
    for index, (opened, close, mid) in enumerate(zip(opens, closes, middle)):
        rows.append({
            "timestamp": (index + 1) * 60_000,
            "open": opened, "close": close,
            "high": max(opened, close) + 0.05,
            "low": min(opened, close) - 0.05,
            "ma3": 100.0, "ma15": 100.0,
            "kc_middle": mid, "kc_upper": mid + 2.0,
            "kc_lower": mid - 2.0, "is_closed": True,
        })
    frame = pd.DataFrame(rows)
    frame.attrs.update(timeframe_ms=60_000, snapshot_ms=240_000)
    position = {"side": side, "open_timestamp": 0.0, "entry_price": 100.0}
    assert evaluate_trend_lock_exit(position, frame) is None


def test_kc_midline_break_during_counter_color_pullback_does_not_exit():
    frame = frame_for(
        "LONG", prior_open=101.0, prior_close=100.5,
        current_open=100.5, current_close=99.5,
        middle=(101.0, 100.0),
    )
    position = {"side": "LONG", "open_timestamp": 0.0, "entry_price": 100.0}
    assert evaluate_trend_lock_exit(position, frame) is None


def test_missing_snapshot_clock_fails_closed_without_accumulating_outer_touch():
    frame = frame_for("LONG", current_high=103.0, upper=(102.0, 102.0))
    frame.attrs.pop("snapshot_ms")
    position = {"side": "LONG", "open_timestamp": 0.0, "entry_price": 100.0}
    assert evaluate_trend_lock_exit(position, frame) is None
    assert not position.get("trend_lock_state", {}).get("outer_rail_touched", False)


@pytest.mark.parametrize("side,ma3,ma15,reason", [
    ("LONG", (101.0, 98.0), (100.0, 99.0), "MA3_MA15_DEATH_CROSS"),
    ("SHORT", (99.0, 102.0), (100.0, 101.0), "MA3_MA15_GOLDEN_CROSS"),
])
def test_directional_ma_cross_alone_does_not_authorize_exit(side, ma3, ma15, reason):
    frame = frame_for(side, ma3=ma3, ma15=ma15)
    position = {"side": side, "open_timestamp": 0.0, "entry_price": 100.0}
    assert evaluate_trend_lock_exit(position, frame) is None


@pytest.mark.parametrize("side,kwargs,reason", [
    ("LONG", {"prior_open": 99.0, "prior_close": 101.0,
               "current_open": 101.0, "current_close": 99.5,
               "prior_high": 103.0, "current_high": 101.2,
               "upper": (102.0, 102.0)}, "OUTER_RAIL_BEARISH_ENGULFING"),
    ("LONG", {"current_open": 101.0, "current_close": 100.0,
               "current_high": 103.0, "current_low": 99.8,
               "upper": (102.0, 102.0)}, "EXTREME_UPPER_WICK"),
    ("SHORT", {"prior_open": 101.0, "prior_close": 99.0,
                "current_open": 99.0, "current_close": 100.5,
                "prior_low": 97.0, "current_low": 98.8,
                "lower": (98.0, 98.0)}, "OUTER_RAIL_BULLISH_ENGULFING"),
    ("SHORT", {"current_open": 99.0, "current_close": 100.0,
                "current_high": 100.2, "current_low": 97.0,
                "lower": (98.0, 98.0)}, "EXTREME_LOWER_WICK"),
])
def test_outer_reversal_or_extreme_wick_without_confirmed_pivot_does_not_exit(side, kwargs, reason):
    frame = frame_for(side, **kwargs)
    position = {
        "side": side, "open_timestamp": 0.0, "entry_price": 100.0,
        "trend_lock_state": {"outer_rail_touched": True},
    }
    assert evaluate_trend_lock_exit(position, frame) is None


@pytest.mark.parametrize("side,close,reason", [
    ("LONG", 98.0, "KC_MIDDLE_BREAK"),
    ("SHORT", 102.0, "KC_MIDDLE_BREAK"),
])
def test_closed_kc_midline_break_alone_does_not_authorize_exit(side, close, reason):
    frame = frame_for(side, current_close=close)
    position = {"side": side, "open_timestamp": 0.0, "entry_price": 100.0}
    assert evaluate_trend_lock_exit(position, frame) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_confirmed_post_entry_three_bar_pivot_authorizes_exit(side):
    rows = []
    for index in range(15):
        opened, close = 100.0, 100.2
        high, low, ma5, ma15 = 101.0, 99.0, 100.0, 99.5
        if index == 12:
            opened, close, high, low, ma5 = 102.0, 103.0, 104.0, 101.5, 103.0
        elif index == 13:
            opened, close, high, low, ma5 = 103.0, 102.5, 105.0, 102.0, 102.5
        elif index == 14:
            opened, close, high, low, ma5 = 102.0, 100.0, 102.2, 99.8, 101.0
        if side == "SHORT":
            opened, close = 200.0 - opened, 200.0 - close
            high, low = 200.0 - low, 200.0 - high
            ma5, ma15 = 200.0 - ma5, 200.0 - ma15
        rows.append({
            "timestamp": (index + 1) * 60_000,
            "open": opened, "high": high, "low": low, "close": close,
            "ma3": ma5, "ma5": ma5, "ma15": ma15,
            "kc_upper": max(opened, close) + 5,
            "kc_middle": (opened + close) / 2,
            "kc_lower": min(opened, close) - 5,
            "is_closed": True,
        })
    frame = pd.DataFrame(rows)
    frame.attrs.update(timeframe_ms=60_000, snapshot_ms=960_000)
    position = {
        "side": side, "open_timestamp": 60.0,
        "entry_price": 100.0 if side == "LONG" else 100.0,
    }
    result = evaluate_trend_lock_exit(position, frame)
    assert result is not None
    assert result["reason"] == (
        "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT" if side == "LONG"
        else "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
    )
    assert result["confirmed_pivot"]["confirm_bar_id"] == 900_000


def test_unclosed_bar_and_missing_indicators_fail_closed():
    frame = frame_for("LONG", current_close=98.0, current_closed=False)
    position = {"side": "LONG", "open_timestamp": 0.0, "entry_price": 100.0}
    assert evaluate_trend_lock_exit(position, frame) is None
    frame.loc[1, "ma3"] = float("nan")
    frame.loc[1, "kc_middle"] = float("nan")
    assert evaluate_trend_lock_exit(position, frame) is None


def test_fixed_atr_profit_reason_is_not_trend_lock_exit():
    frame = frame_for("LONG")
    position = {"side": "LONG", "open_timestamp": 0.0, "entry_price": 100.0}
    assert evaluate_trend_lock_exit(position, frame) is None
    from core.services.exits.authorized_position_exit import automated_position_exit_allowed
    assert not automated_position_exit_allowed(
        "TEST/USDT", position, {}, "EXIT_BY_FIXED_ATR_TAKE_PROFIT", 101.5,
        frame=frame,
    )


def test_exit_gate_suppresses_legacy_profit_exit_until_trend_lock(monkeypatch):
    from core.gates import unified_pipeline_gates as gates

    frame = frame_for("LONG")
    position = {"side": "LONG", "entry_price": 100.0, "open_timestamp": 0.0}
    market_data = {"current_price": 101.5, "raw_df": frame}
    monkeypatch.setattr(
        gates.ExitGate, "_evaluate_with_holding_rules",
        staticmethod(lambda *args, **kwargs: (
            True, "EXIT_BY_FIXED_ATR_TAKE_PROFIT", {"atr_multiple": 1.5},
        )),
    )
    should_exit, reason, meta = gates.ExitGate.evaluate(position, market_data)
    assert should_exit is False
    assert reason == "VETO_TREND_LOCK"
    assert meta["suppressed_exit_reason"] == "EXIT_BY_FIXED_ATR_TAKE_PROFIT"


def test_exit_gate_keeps_emergency_result(monkeypatch):
    from core.gates import unified_pipeline_gates as gates

    monkeypatch.setattr(
        gates.ExitGate, "_evaluate_with_holding_rules",
        staticmethod(lambda *args, **kwargs: (
            True, "EXIT_BY_EXTREME_WATERFALL", {"emergency": True},
        )),
    )
    result = gates.ExitGate.evaluate(
        {"side": "LONG", "entry_price": 100.0},
        {"current_price": 80.0, "raw_df": pd.DataFrame()},
    )
    assert result[0] is True
    assert result[1] == "EXIT_BY_EXTREME_WATERFALL"


def test_emergency_exit_remains_independent_of_trend_lock():
    from core.services.exits.trend_lock import is_trend_lock_emergency
    from core.services.exits.authorized_position_exit import automated_position_exit_allowed
    import core.services.math_execution_gate as math_gate

    assert is_trend_lock_emergency("ACCOUNT_HARD_STOP")
    assert is_trend_lock_emergency("EXIT_BY_EXTREME_WATERFALL")
    assert not is_trend_lock_emergency("EXIT_BY_FIXED_ATR_TAKE_PROFIT")
    original = math_gate.validate_exit_math
    math_gate.validate_exit_math = lambda *args, **kwargs: (True, "EMERGENCY", {})
    try:
        assert automated_position_exit_allowed(
            "TEST/USDT", {"side": "LONG", "entry_price": 100.0}, {},
            "ACCOUNT_HARD_STOP", 80.0,
        )
    finally:
        math_gate.validate_exit_math = original


def test_same_cycle_is_consumed_but_new_ma_cross_can_reenter():
    state = {}
    first = frame_for("LONG", ma3=(99.0, 101.0), ma15=(100.0, 100.0))
    allowed, _, cycle = evaluate_trend_cycle_entry(state, first, "LONG")
    assert allowed is True
    claim_trend_cycle(state, "LONG", cycle)
    assert evaluate_trend_cycle_entry(state, first, "LONG")[0] is False

    next_cycle = frame_for(
        "LONG", ma3=(99.0, 101.0), ma15=(100.0, 100.0),
    )
    next_cycle.loc[1, "timestamp"] = 180_000
    next_cycle.loc[0, "timestamp"] = 120_000
    next_cycle.attrs["timeframe_ms"] = 60_000
    next_cycle.attrs["snapshot_ms"] = 240_000
    allowed, _, new_cycle = evaluate_trend_cycle_entry(state, next_cycle, "LONG")
    assert allowed is True
    assert new_cycle != cycle


def test_trend_cycle_requires_closed_data_and_handles_duplicate_claims():
    state = {}
    frame = frame_for(
        "SHORT", ma3=(101.0, 99.0), ma15=(100.0, 100.0),
        current_closed=False,
    )
    assert evaluate_trend_cycle_entry(state, frame, "SHORT")[0] is False
    frame.loc[1, "is_closed"] = True
    allowed, _, cycle = evaluate_trend_cycle_entry(state, frame, "SHORT")
    assert allowed is True
    claim_trend_cycle(state, "SHORT", cycle)
    claim_trend_cycle(state, "SHORT", cycle)
    assert evaluate_trend_cycle_entry(state, frame, "SHORT")[0] is False


def test_outer_rail_touch_and_cycle_lock_survive_restart_state_copy():
    import copy
    from core.services.exits.trend_lock import update_trend_lock_state

    position = {"side": "SHORT", "entry_price": 100.0, "open_timestamp": 0.0}
    touch_frame = frame_for(
        "SHORT", current_low=97.5, lower=(98.0, 98.0),
        middle=(101.0, 101.0), current_close=99.0,
        current_open=99.2,
    )
    assert update_trend_lock_state(position, touch_frame) is True
    restored_position = copy.deepcopy(position)
    reversal = frame_for(
        "SHORT", prior_open=101.0, prior_close=99.0,
        current_open=99.0, current_close=100.5,
        prior_low=97.0, current_low=98.8,
        lower=(98.0, 98.0), middle=(101.0, 101.0),
    )
    decision = evaluate_trend_lock_exit(restored_position, reversal)
    assert decision is None
    assert restored_position["trend_lock_state"]["outer_rail_touched"] is True


def test_cycle_claim_is_idempotent_for_partial_fill_recovery():
    from core.services.exits.trend_lock import claim_filled_trend_cycle

    class Account:
        entry_filter_last = {}
        saves = 0

        def save_state(self, **kwargs):
            self.saves += 1

    account = Account()
    assert claim_filled_trend_cycle(account, "1000PEPE/USDT", "SHORT", "KC_SHORT_BREAKOUT:120000")
    assert claim_filled_trend_cycle(account, "1000PEPE/USDT", "SHORT", "KC_SHORT_BREAKOUT:120000")
    state = account.entry_filter_last
    assert state["trend_cycle_lock"]["1000PEPE/USDT"]["60000ms"]["SHORT"] == "KC_SHORT_BREAKOUT:120000"
    assert account.saves == 2


def test_trend_cycles_are_isolated_by_symbol_side_and_timeframe():
    from core.services.exits.trend_lock import claim_trend_cycle

    state = {}
    minute = frame_for("LONG", ma3=(99.0, 101.0), ma15=(100.0, 100.0))
    allowed, _, cycle = evaluate_trend_cycle_entry(state, minute, "LONG", "A/USDT")
    assert allowed
    claim_trend_cycle(state, "LONG", cycle, "A/USDT", "60000ms")
    assert not evaluate_trend_cycle_entry(state, minute, "LONG", "A/USDT")[0]
    short_cycle = frame_for("SHORT", ma3=(101.0, 99.0), ma15=(100.0, 100.0))
    assert evaluate_trend_cycle_entry(state, short_cycle, "SHORT", "A/USDT")[0]
    assert evaluate_trend_cycle_entry(state, minute, "LONG", "B/USDT")[0]
    five_minute = minute.copy()
    five_minute.loc[:, "timestamp"] = [0, 300_000]
    five_minute.attrs["timeframe_ms"] = 300_000
    five_minute.attrs["snapshot_ms"] = 600_000
    assert evaluate_trend_cycle_entry(state, five_minute, "LONG", "A/USDT")[0]


def test_opposite_cycle_ends_same_side_reentry_until_a_fresh_start_event():
    state = {}
    long_cycle = frame_for("LONG", ma3=(99.0, 101.0), ma15=(100.0, 100.0))
    allowed, _, key = evaluate_trend_cycle_entry(state, long_cycle, "LONG")
    assert allowed
    from core.services.exits.trend_lock import claim_trend_cycle
    claim_trend_cycle(state, "LONG", key)

    reversed_frame = frame_for(
        "LONG", ma3=(101.0, 99.0), ma15=(100.0, 100.0),
    )
    allowed, reason, _ = evaluate_trend_cycle_entry(state, reversed_frame, "LONG")
    assert not allowed
    assert reason == "BLOCKED_TREND_CYCLE_REVERSED"


def test_stale_closed_snapshot_cannot_authorize_exit_or_entry():
    frame = frame_for("LONG", ma3=(101.0, 98.0), ma15=(100.0, 99.0))
    frame.attrs["snapshot_ms"] = 500_000
    position = {"side": "LONG", "entry_price": 100.0, "open_timestamp": 0.0}
    assert evaluate_trend_lock_exit(position, frame) is None
    assert evaluate_trend_cycle_entry({}, frame, "LONG")[0] is False


def test_paper_and_testnet_partial_close_boundaries_reject_fixed_tp_without_trend_lock():
    from core.paper_account import PaperAccount
    from core.testnet_account import BinanceTestnetAccount

    for account_type in (PaperAccount, BinanceTestnetAccount):
        account = object.__new__(account_type)
        account.positions = {"TEST/USDT": {
            "side": "LONG", "entry_price": 100.0, "qty": 1.0,
            "margin": 10.0, "open_timestamp": 0.0,
        }}
        account.position_meta = {"TEST/USDT": {}}
        account.closing_lock = set()
        account._channel_exit_frames = {"TEST/USDT": frame_for("LONG")}
        account.log = lambda *args, **kwargs: None
        assert asyncio.run(account.partial_close_position(
            "TEST/USDT", 101.5, "TAKE_PROFIT_50PCT", fraction=0.5,
        )) is False


def test_account_circuit_breaker_flattens_managed_symbols_independent_of_trend_lock():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from core.testnet_account import BinanceTestnetAccount

    account = object.__new__(BinanceTestnetAccount)
    account._circuit_breaker_running = False
    account.circuit_breaker_status = "LATCHED"
    account.exchange = SimpleNamespace(
        fapiPrivateV2GetPositionRisk=AsyncMock(side_effect=[
            [{"symbol": "1000PEPEUSDT", "positionAmt": "2"}], []
        ]),
        fetch_open_orders=AsyncMock(return_value=[]),
    )
    account._raw_create_order = AsyncMock(return_value={"id": "risk-close"})
    account.log = Mock()
    account.save_state = Mock()
    result = asyncio.run(account._run_circuit_breaker_flatten())
    assert result["close_attempts"] == [{
        "symbol": "1000PEPE/USDT", "qty": "2", "ok": True,
    }]
    account._raw_create_order.assert_awaited_once()


def test_rapid_adverse_emergency_requires_configured_price_evidence():
    from core.services.math_execution_gate import validate_exit_math

    position = {
        "side": "LONG", "entry_price": 100.0, "entry_atr": 2.0,
        "open_timestamp": 0.0,
    }
    reason = "緊急斷路:急速反向 2.0ATR(>1.5ATR)"
    assert validate_exit_math(position, None, 96.0, close_reason=reason)[0]
    assert not validate_exit_math(position, None, 98.0, close_reason=reason)[0]


@pytest.mark.parametrize("price,expected", [(94.0, True), (98.0, False)])
def test_data_outage_emergency_requires_hard_loss_price_evidence(price, expected):
    from core.services.math_execution_gate import validate_exit_math

    position = {
        "side": "LONG", "entry_price": 100.0, "entry_atr": 1.0,
        "leverage": 10.0,
    }
    allowed, _code, _evidence = validate_exit_math(
        position, None, price,
        close_reason="CIRCUIT_BREAKER_EMERGENCY_DATA_OUTAGE",
    )
    assert allowed is expected


def test_startup_cleanup_cancels_managed_take_profits_and_preserves_stops():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from core.testnet_account import BinanceTestnetAccount

    class Exchange:
        def __init__(self):
            self.normal = [
                {"id": "tp-normal", "type": "TAKE_PROFIT_MARKET"},
                {"id": "sl-normal", "type": "STOP_MARKET"},
                {"id": "trail-normal", "type": "TRAILING_STOP_MARKET"},
            ]
            self.algo = [
                {"algoId": "tp-algo", "type": "TAKE_PROFIT_MARKET", "algoStatus": "WORKING"},
                {"algoId": "sl-algo", "type": "STOP_MARKET", "algoStatus": "WORKING"},
                {"algoId": "trail-algo", "type": "TRAILING_STOP_MARKET", "algoStatus": "WORKING"},
            ]

        async def fetch_open_orders(self, _symbol):
            return list(self.normal)

        async def cancel_order(self, order_id, _symbol):
            self.normal = [order for order in self.normal if order["id"] != order_id]

        async def request(self, endpoint, _api, method, params):
            if endpoint == "algoOpenOrders" and method == "GET":
                return list(self.algo)
            if endpoint == "algoOrder" and method == "DELETE":
                self.algo = [order for order in self.algo if order["algoId"] != params["algoId"]]
                return {"algoId": params["algoId"], "algoStatus": "CANCELED"}
            raise AssertionError((endpoint, method))

    account = object.__new__(BinanceTestnetAccount)
    account.positions = {"1000PEPE/USDT": {"managed_by_bot": True, "side": "LONG"}}
    account.position_meta = {}
    account.exchange = Exchange()
    account.log = lambda *args, **kwargs: None
    assert asyncio.run(account._cancel_trend_lock_native_profit_exits(
        {"1000PEPE/USDT"},
    )) is True
    assert [order["id"] for order in account.exchange.normal] == ["sl-normal"]
    assert [order["algoId"] for order in account.exchange.algo] == ["sl-algo"]
    asyncio.run(account._remove_channel_native_exits("1000PEPE/USDT"))
    assert [order["id"] for order in account.exchange.normal] == ["sl-normal"]
    assert [order["algoId"] for order in account.exchange.algo] == ["sl-algo"]


def test_initial_stop_recovery_keeps_existing_exchange_stop(monkeypatch):
    from core import testnet_account as testnet_module
    from core.testnet_account import BinanceTestnetAccount

    class Exchange:
        def __init__(self):
            self.normal = [{"id": "sl-existing", "type": "STOP_MARKET"}]
            self.cancel_calls = 0

        async def fetch_open_orders(self, _symbol):
            return list(self.normal)

        async def request(self, endpoint, _api, method, _params):
            assert (endpoint, method) == ("algoOpenOrders", "GET")
            return []

        async def cancel_all_orders(self, _symbol):
            self.cancel_calls += 1
            self.normal.clear()

    monkeypatch.setattr(testnet_module, "POSITION_HARD_STOPS_ENABLED", True)
    monkeypatch.setattr(testnet_module, "ENABLE_EXCHANGE_INITIAL_STOP_LOSS", True)
    account = object.__new__(BinanceTestnetAccount)
    account.positions = {
        "PEPE/USDT": {
            "side": "LONG", "entry_mode": "CHANNEL_SWING", "qty": 2.0,
            "entry_price": 100.0, "leverage": 2.0, "sl": 95.0,
        },
    }
    account.position_meta = {"PEPE/USDT": {"sl": 95.0}}
    account.exchange = Exchange()
    account._price_to_precision = lambda _symbol, price: str(price)
    account._create_protection_order = lambda *_args: pytest.fail(
        "must not create a duplicate stop when one already exists"
    )
    account.log = lambda *_args, **_kwargs: None
    assert asyncio.run(account._restore_exchange_initial_stops()) is True
    assert account.exchange.cancel_calls == 0
    assert account.exchange.normal == [{"id": "sl-existing", "type": "STOP_MARKET"}]


def test_initial_stop_recovery_restores_hard_stop_after_trailing_cleanup(
    monkeypatch,
):
    from core import testnet_account as testnet_module
    from core.testnet_account import BinanceTestnetAccount

    class Exchange:
        async def fetch_open_orders(self, _symbol):
            return []

        async def request(self, endpoint, _api, method, _params):
            assert (endpoint, method) == ("algoOpenOrders", "GET")
            return []

    monkeypatch.setattr(testnet_module, "POSITION_HARD_STOPS_ENABLED", True)
    monkeypatch.setattr(testnet_module, "ENABLE_EXCHANGE_INITIAL_STOP_LOSS", True)
    account = object.__new__(BinanceTestnetAccount)
    account.positions = {
        "PEPE/USDT": {
            "side": "LONG", "entry_mode": "CHANNEL_SWING", "qty": 2.0,
            "entry_price": 100.0, "leverage": 2.0, "initial_sl": 95.0,
        },
    }
    account.position_meta = {
        "PEPE/USDT": {"native_trailing_tier": 2},
    }
    account.exchange = Exchange()
    account._price_to_precision = lambda _symbol, price: str(price)
    created = []

    async def create_stop(symbol, side, order_type, qty, price):
        created.append((symbol, side, order_type, qty, price))
        return {"algoId": "restored-stop"}

    account._create_protection_order = create_stop
    account.log = lambda *_args, **_kwargs: None
    assert asyncio.run(account._restore_exchange_initial_stops()) is True
    assert len(created) == 1
    symbol, side, order_type, qty, stop_price = created[0]
    assert (symbol, side, order_type, qty) == (
        "PEPE/USDT", "sell", "STOP_MARKET", 2.0,
    )
    assert 0 < stop_price < 100.0


@pytest.mark.anyio
async def test_confirmed_exit_executor_submits_once_and_requires_filled_terminal_order():
    from core.gates.trend_riding_strategy import AntiSlippageOrderExecutor

    class Exchange:
        def price_to_precision(self, _symbol, price):
            return str(price)

        async def fetch_order(self, _order_id, _symbol):
            return {
                "id": "close-1", "status": "closed", "filled": 2.0,
                "remaining": 0.0, "average": 99.5,
            }

    class Account:
        is_paper = False

        def __init__(self):
            self.exchange = Exchange()
            self.position_meta = {}
            self.orders = []

        async def _send_order(self, *args):
            self.orders.append(args)
            return {"id": "close-1", "status": "open", "filled": 0.0}

        def log(self, *_args, **_kwargs):
            return None

        def save_state(self):
            return None

    account = Account()
    filled, order = await AntiSlippageOrderExecutor.execute_confirmed_limit_close(
        account, "PEPE/USDT", "LONG", 2.0, 100.0,
        watchdog_timeout_sec=0.1,
    )
    assert filled is True
    assert order["filled"] == 2.0
    assert len(account.orders) == 1


@pytest.mark.anyio
async def test_testnet_close_uses_one_confirmed_order_and_books_only_that_fill(
    monkeypatch, tmp_path,
):
    from unittest.mock import AsyncMock, Mock
    import core.testnet_account as testnet_module
    from core.gates.trend_riding_strategy import AntiSlippageOrderExecutor
    from core.testnet_account import BinanceTestnetAccount

    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "state.json"))
    account = object.__new__(BinanceTestnetAccount)
    account.positions = {
        "PEPE/USDT": {
            "side": "LONG", "entry_price": 100.0, "qty": 2.0,
            "margin": 100.0, "leverage": 2.0, "open_timestamp": 1.0,
            "trend_lock_managed": True,
        },
    }
    account.position_meta = {"PEPE/USDT": {}}
    account.closing_lock = set()
    account.pending_limit_orders = {}
    account.last_closed_at = {}
    account.last_closed_side = {}
    account.last_closed_price = {}
    account._close_retry_after = {}
    account._auto_close_reject_logged_at = {}
    account.trades = []
    account.realized_pnl = 0.0
    account.on_trade_closed = None
    account.log = Mock()
    account.save_state = Mock()
    account.refresh = AsyncMock()
    events = []

    async def cancel_all_orders(_symbol):
        events.append("post_fill_cleanup")

    async def cancel_native_profit_exits(_symbols):
        events.append("preserve_hard_stop_cleanup")
        return True

    account._cancel_all_orders = AsyncMock(side_effect=cancel_all_orders)
    account._cancel_trend_lock_native_profit_exits = AsyncMock(
        side_effect=cancel_native_profit_exits,
    )
    submitted = []

    async def confirmed(*, account, symbol, side, qty, live_price, **_kwargs):
        events.append("confirmed_close_order")
        submitted.append((symbol, side, qty))
        return True, {
            "id": "one-close", "status": "CLOSED", "filled": qty,
            "amount": qty, "remaining": 0.0, "average": 99.5,
        }

    monkeypatch.setattr(
        AntiSlippageOrderExecutor,
        "execute_confirmed_limit_close",
        classmethod(lambda cls, **kwargs: confirmed(**kwargs)),
    )
    monkeypatch.setattr(
        AntiSlippageOrderExecutor,
        "execute_anti_slippage_close",
        classmethod(lambda *_args, **_kwargs: pytest.fail(
            "legacy watchdog must not submit a second close order"
        )),
    )

    closed = await account.close_position(
        "PEPE/USDT", 100.0, "手動平倉", is_manual=True,
    )
    assert closed is True
    assert submitted == [("PEPE/USDT", "LONG", 2.0)]
    assert events == [
        "preserve_hard_stop_cleanup", "confirmed_close_order",
        "post_fill_cleanup",
    ]
    assert "PEPE/USDT" not in account.positions
    assert account.trades[0]["exchange_order_id"] == "one-close"


@pytest.mark.parametrize("order,expected", [
    ({"status": "closed", "filled": 0.4, "amount": 1.0}, 0.4),
    ({"status": "canceled", "filled": 0.25, "amount": 1.0}, 0.25),
    ({"status": "open", "filled": 1.0, "amount": 1.0}, None),
    ({"status": "canceled", "filled": 0.0, "amount": 1.0}, None),
    ({"status": "closed", "filled": 1.1, "amount": 1.0}, None),
    ({"status": "closed", "filled": "nan", "amount": 1.0}, None),
])
def test_market_entry_cycle_requires_terminal_confirmed_fill(order, expected):
    from core.testnet_account import _confirmed_market_entry_fill

    assert _confirmed_market_entry_fill(order, 1.0) == expected


@pytest.mark.parametrize("position,meta,managed", [
    ({"entry_mode": "CHANNEL_SWING"}, {}, True),
    ({"managed_by_bot": True}, {}, True),
    ({}, {"trend_lock_state": {"side": "SHORT"}}, True),
    ({"entry_mode": "MANUAL"}, {}, False),
])
def test_native_take_profit_classification_matches_trend_lock_ownership(
    position, meta, managed,
):
    from core.testnet_account import _is_trend_lock_managed

    assert _is_trend_lock_managed(position, meta) is managed


def test_unverified_native_exit_cleanup_blocks_new_entries():
    from types import SimpleNamespace
    from core.services.math_execution_gate import validate_entry_at_execution

    account = SimpleNamespace(
        _trend_lock_native_exit_cleanup_complete=False,
        entry_filter_last={}, trades=[],
    )
    allowed, reason = asyncio.run(validate_entry_at_execution(
        account, "A/USDT", "LONG", {"entry_gate_frame": frame_for("LONG")},
    ))
    assert allowed is False
    assert reason == "BLOCKED_TREND_LOCK_NATIVE_EXIT_CLEANUP_UNVERIFIED"


def test_entry_rejection_does_not_consume_trend_cycle_before_fill(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from core.gates.central_entry_gate import CentralEntryGate, GateCheckResult
    from core.services.math_execution_gate import validate_entry_at_execution

    frame = frame_for("LONG", ma3=(99.0, 101.0), ma15=(100.0, 100.0))
    account = SimpleNamespace(entry_filter_last={}, trades=[])
    context = {"entry_gate_frame": frame, "entry_gate_quote": 100.0}
    monkeypatch.setattr(
        CentralEntryGate, "verify_and_authorize",
        classmethod(lambda cls, **kwargs: GateCheckResult(
            False, "SYNTHETIC_REJECTION", "signal",
        )),
    )
    allowed, reason = asyncio.run(validate_entry_at_execution(
        account, "A/USDT", "LONG", context,
    ))
    assert not allowed
    assert "SYNTHETIC_REJECTION" in reason
    assert context["trend_lock_managed"] is True
    assert account.entry_filter_last == {}


def test_execution_math_gate_rejects_midline_and_ma_cross_without_true_pivot():
    from core.services.math_execution_gate import validate_exit_at_execution

    class Account:
        position_meta = {"TEST/USDT": {}}
        _channel_exit_frames = {}

    account = Account()
    frame = frame_for("LONG")
    account._channel_exit_frames["TEST/USDT"] = frame
    position = {"side": "LONG", "entry_price": 100.0, "open_timestamp": 0.0}
    assert validate_exit_at_execution(
        account, "TEST/USDT", position, 100.0, "EXIT_BY_FIXED_ATR_TAKE_PROFIT",
    )[0] is False
    account._channel_exit_frames["TEST/USDT"] = frame_for(
        "LONG", ma3=(101.0, 98.0), ma15=(100.0, 99.0),
    )
    allowed, code, evidence = validate_exit_at_execution(
        account, "TEST/USDT", position, 100.0, "EXIT_BY_TREND_LOCK_MA3_MA15_DEATH_CROSS",
    )
    assert allowed is False
    assert code == "BLOCKED_TREND_LOCK_OR_UNCONFIRMED_EMERGENCY"
    assert evidence["exit_type"] == "BLOCKED"


def test_initial_hard_stop_survives_trend_lock_without_closed_strategy_signal():
    from core.services.math_execution_gate import validate_exit_at_execution

    class Account:
        position_meta = {"TEST/USDT": {}}
        _channel_exit_frames = {}

    account = Account()
    position = {
        "side": "LONG", "entry_price": 100.0, "open_timestamp": 0.0,
        "initial_sl": 90.0,
    }
    allowed, code, evidence = validate_exit_at_execution(
        account, "TEST/USDT", position, 89.0, "觸發止損 (Stop-Loss)",
    )
    assert allowed is True
    assert code == "EMERGENCY_EXIT_HARD_STOP_CONFIRMED"
    assert evidence["exit_type"] == "HARD_STOP"


def test_chinese_hard_stop_label_requires_and_accepts_breached_stop_evidence():
    from core.services.math_execution_gate import validate_exit_at_execution

    class Account:
        position_meta = {"TEST/USDT": {"initial_sl": 90.0}}
        _channel_exit_frames = {}
        log = lambda *args: None

    position = {
        "side": "LONG", "entry_price": 100.0, "open_timestamp": 0.0,
        "initial_sl": 90.0, "sl": 90.0, "qty": 1.0,
    }
    allowed, code, _ = validate_exit_at_execution(
        Account(), "TEST/USDT", position, 89.0, "觸發止損 (Stop-Loss)",
    )
    assert allowed is True
    assert code == "EMERGENCY_EXIT_HARD_STOP_CONFIRMED"


@pytest.mark.parametrize("reason", [
    "EXIT_BY_TRAILING_PROFIT_LOCK", "HARD_STOP_LOSS_TRAILING_LOCK",
])
def test_favorable_trailing_stop_cannot_be_relabelled_as_initial_hard_stop(reason):
    from core.services.math_execution_gate import validate_exit_at_execution

    class Account:
        position_meta = {"TEST/USDT": {"sl": 101.0, "is_breakeven_moved": True}}
        _channel_exit_frames = {"TEST/USDT": frame_for("LONG")}
        log = lambda *args: None

    position = {
        "side": "LONG", "entry_price": 100.0, "open_timestamp": 0.0,
        "qty": 1.0, "sl": 101.0, "is_breakeven_moved": True,
    }
    allowed, _, _ = validate_exit_at_execution(
        Account(), "TEST/USDT", position, 100.5, reason,
    )
    assert allowed is False
