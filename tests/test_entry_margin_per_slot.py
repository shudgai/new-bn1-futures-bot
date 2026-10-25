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
def test_wild_swing_symbols_use_full_wallet_in_one_slot(
    monkeypatch, symbol
):
    """2026-10-21：WILD_SWING_SYMBOL_MARGIN_FRACTION 改為 0.98，1槽使用帳戶餘額而非 50%。"""
    monkeypatch.setattr(config, "MAX_SLOTS", 1)
    monkeypatch.setattr(config, "WILD_SWING_SYMBOL_MARGIN_FRACTION", 0.98)

    leverage = 2.
    fee_buffer = 1.0 + leverage * config.TAKER_FEE_RATE
    expected = min(
        100. * 0.98,                        # per_slot_budget (1 slot)
        (100. / fee_buffer) * 0.98,          # fee_safe_available
        100. * 0.98,                          # symbol_budget = wallet * 0.98
    )
    amount = TradingEngine._full_wallet_entry_margin(100., 100., leverage, symbol)

    assert amount == pytest.approx(expected)



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
    import core.services.entry_firewall as entry_firewall
    import core.symbol_aliases as symbol_aliases

    monkeypatch.setattr(config, "MAX_SLOTS", 2)
    monkeypatch.setattr(engine_module, "MAX_SLOTS", 2)
    # This test isolates the account reservation/slot race. Entry qualification
    # and the currently enabled-symbol policy have their own gate tests.
    monkeypatch.setattr(symbol_aliases, "is_auto_entry_symbol", lambda _symbol: True)
    monkeypatch.setattr(
        entry_firewall, "has_valid_wild_outer_confirmation",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        entry_firewall, "market_structure_gate_problem",
        lambda *_args, **_kwargs: None,
    )
    engine = object.__new__(TradingEngine)
    engine.account = BudgetAccount(150.)
    engine.symbol_rotation = SimpleNamespace(
        get_dynamic_leverage=lambda *_args: 5,
    )
    frame = pd.DataFrame([{
        "timestamp": 1_000, "atr": 1., "kc_upper": 99., "kc_lower": 90.,
    }])

    async def submit_pair():
        pair = await asyncio.gather(
            engine.execute_dual_tf_entry(
                LOBSTER, frame, 100., "LONG", "CROSSOVER",
                {"bar_id": 1_000, "entry_pattern": "CONTINUATION_OUTSIDE_KC"}, "WILD_SWING",
            ),
            engine.execute_dual_tf_entry(
                PEPE, frame, 100., "LONG", "CROSSOVER",
                {"bar_id": 1_000, "entry_pattern": "CONTINUATION_OUTSIDE_KC"}, "WILD_SWING",
            ),
        )
        third = await engine.execute_dual_tf_entry(
            "DOGE/USDT", frame, 100., "LONG", "CROSSOVER",
            {"bar_id": 1_000, "entry_pattern": "CONTINUATION_OUTSIDE_KC"}, "WILD_SWING",
        )
        return pair, third

    pair, third = asyncio.run(submit_pair())

    assert pair == [True, True]
    assert third is False
    assert {order["symbol"] for order in engine.account.opened} == {
        LOBSTER, PEPE,
    }
    # 2 槽時每槽 budget = wallet * 0.98 / 2 = 75，各符合 <= 75
    assert all(order["amount_usdt"] <= 75. for order in engine.account.opened)
    assert engine.account.available >= 0.


def test_direct_entry_rechecks_slot_limit_before_order_submission(monkeypatch):
    import core.engine as engine_module
    import core.symbol_aliases as symbol_aliases

    monkeypatch.setattr(config, "MAX_SLOTS", 2)
    monkeypatch.setattr(engine_module, "MAX_SLOTS", 2)
    monkeypatch.setattr(symbol_aliases, "is_auto_entry_symbol", lambda _symbol: True)
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
