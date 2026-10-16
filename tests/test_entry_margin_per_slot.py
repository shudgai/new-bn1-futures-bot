import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

import core.config as config
from core.engine import TradingEngine

LOBSTER = "龍蝦/USDT"
PEPE = "1000PEPE/USDT"


class BudgetAccount:
    def __init__(self, wallet):
        self.wallet = wallet
        self.available = wallet
        self.positions = {}
        self.pending_limit_orders = {}
        self.closing_lock = set()
        self.opened = []
        self.log = Mock()

    def get_wallet_balance(self):
        return self.wallet

    def get_available_balance(self):
        return self.available

    async def open_position(self, **order):
        await asyncio.sleep(0)
        cost = order["amount_usdt"] * (
            1. + order["leverage"] * config.TAKER_FEE_RATE
        )
        if cost > self.available:
            return False
        self.available -= cost
        self.opened.append(order)
        self.positions[order["symbol"]] = {"side": order["side"]}
        return True


@pytest.mark.parametrize(
    ("slot_count", "expected"),
    [(1, 100.), (2, 50.)],
)
def test_entry_margin_respects_configured_slot_budget(
    monkeypatch, slot_count, expected
):
    monkeypatch.setattr(config, "MAX_SLOTS", slot_count)

    amount = TradingEngine._full_wallet_entry_margin(100., 100., 2.)

    expected = min(
        expected * 0.98,
        100. / (1. + 2. * config.TAKER_FEE_RATE) * 0.98,
    )
    assert amount == pytest.approx(expected)


def test_entry_margin_never_exceeds_available_balance_after_fee(monkeypatch):
    monkeypatch.setattr(config, "MAX_SLOTS", 2)

    amount = TradingEngine._full_wallet_entry_margin(100., 20., 2.)

    assert amount == pytest.approx(
        20. / (1. + 2. * config.TAKER_FEE_RATE) * 0.98
    )


@pytest.mark.parametrize(
    "symbol",
    [LOBSTER, "龙虾/USDT", "LOBSTER/USDT", PEPE, "1000PEPEUSDT"],
)
def test_wild_swing_symbols_are_individually_capped_at_half_wallet(
    monkeypatch, symbol
):
    monkeypatch.setattr(config, "MAX_SLOTS", 1)

    amount = TradingEngine._full_wallet_entry_margin(100., 100., 2., symbol)

    assert amount == pytest.approx(50.)


@pytest.mark.parametrize("symbol", [LOBSTER, PEPE])
def test_two_symbol_budget_fits_available_wallet_after_both_fees(
    monkeypatch, symbol
):
    monkeypatch.setattr(config, "MAX_SLOTS", 2)
    wallet = 150.
    leverage = 5.
    fee_rate = config.TAKER_FEE_RATE
    first_margin = TradingEngine._full_wallet_entry_margin(
        wallet, wallet, leverage, LOBSTER,
    )
    remaining = wallet - first_margin * (1. + leverage * fee_rate)

    second_margin = TradingEngine._full_wallet_entry_margin(
        wallet, remaining, leverage, symbol,
    )

    assert first_margin <= wallet * 0.5
    assert second_margin <= wallet * 0.5
    assert (
        first_margin * (1. + leverage * fee_rate)
        + second_margin * (1. + leverage * fee_rate)
    ) <= wallet


def test_concurrent_wild_swing_entries_share_two_slots_and_wallet_safely(
    monkeypatch,
):
    import core.engine as engine_module

    monkeypatch.setattr(config, "MAX_SLOTS", 2)
    monkeypatch.setattr(engine_module, "MAX_SLOTS", 2)
    engine = object.__new__(TradingEngine)
    engine.account = BudgetAccount(150.)
    engine.symbol_rotation = SimpleNamespace(
        get_dynamic_leverage=lambda *_args: 5,
    )
    frame = pd.DataFrame([{"timestamp": 1_000, "atr": 1.}])

    async def submit_pair():
        pair = await asyncio.gather(
            engine.execute_dual_tf_entry(
                LOBSTER, frame, 100., "LONG", "CROSSOVER",
                {"bar_id": 1_000}, "WILD_SWING",
            ),
            engine.execute_dual_tf_entry(
                PEPE, frame, 100., "LONG", "CROSSOVER",
                {"bar_id": 1_000}, "WILD_SWING",
            ),
        )
        third = await engine.execute_dual_tf_entry(
            "DOGE/USDT", frame, 100., "LONG", "CROSSOVER",
            {"bar_id": 1_000}, "WILD_SWING",
        )
        return pair, third

    pair, third = asyncio.run(submit_pair())

    assert pair == [True, True]
    assert third is False
    assert {order["symbol"] for order in engine.account.opened} == {
        LOBSTER, PEPE,
    }
    assert all(order["amount_usdt"] <= 75. for order in engine.account.opened)
    assert engine.account.available >= 0.


def test_direct_entry_rechecks_slot_limit_before_order_submission(monkeypatch):
    import core.engine as engine_module

    monkeypatch.setattr(config, "MAX_SLOTS", 2)
    monkeypatch.setattr(engine_module, "MAX_SLOTS", 2)
    engine = object.__new__(TradingEngine)
    engine.account = BudgetAccount(500.)
    engine.account.positions.update({
        LOBSTER: {"side": "LONG"},
        PEPE: {"side": "SHORT"},
    })
    engine.symbol_rotation = SimpleNamespace(
        get_dynamic_leverage=lambda *_args: 5,
    )
    frame = pd.DataFrame([{"timestamp": 1_000, "atr": 1.}])

    opened = asyncio.run(
        engine.execute_dual_tf_entry(
            "DOGE/USDT", frame, 100., "LONG", "CROSSOVER",
            {"bar_id": 1_000}, "WILD_SWING",
        )
    )

    assert opened is False
    assert engine.account.opened == []
