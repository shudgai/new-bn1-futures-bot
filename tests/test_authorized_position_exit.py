import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.exits.authorized_position_exit import (
    automated_position_exit_allowed,
)
from core.services.exits.strict_true_pivot import store_confirmed_pivot_exit


def position(side="LONG"):
    return {
        "side": side,
        "open_timestamp": 60.0,
        "entry_price": 100.0,
        "qty": 1.0,
    }


def test_blocks_ordinary_and_loss_exits_for_lobster_and_cap():
    active_position = position()
    for symbol in ("龙虾/USDT", "CAP/USDT"):
        for reason in (
            "觸發止損 (Stop-Loss)",
            "急速逆向閃崩觸發平倉",
            "EXIT_BY_SAME_BAR_PROFIT",
            "TAKE_PROFIT_50PCT",
            "碰觸 KC 上軌平倉",
            "MA5_PULLBACK_EXIT",
        ):
            assert not automated_position_exit_allowed(
                symbol, active_position, {}, reason, 99.0,
            )


def test_allows_positive_net_profit_only_with_armed_profit_lock():
    active_position = position()
    metadata = {"profit_lock_usdt_armed": True}

    assert automated_position_exit_allowed(
        "CAP/USDT", active_position, metadata,
        "NET_ROE_THREE_TIER_GIVEBACK", 102.0,
    )
    assert not automated_position_exit_allowed(
        "CAP/USDT", active_position, metadata,
        "NET_ROE_THREE_TIER_GIVEBACK", 99.0,
    )
    assert not automated_position_exit_allowed(
        "CAP/USDT", active_position, {},
        "NET_ROE_THREE_TIER_GIVEBACK", 102.0,
    )


def test_allows_only_identity_bound_confirmed_pivot_ticket():
    active_position = position()
    assert store_confirmed_pivot_exit(
        active_position,
        (
            "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT",
            {"pivot_bar_id": 120_000, "confirm_bar_id": 180_000},
        ),
    )

    assert automated_position_exit_allowed(
        "龙虾/USDT", active_position, {},
        "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", 100.0,
    )
    changed_position = {**active_position, "qty": 2.0}
    assert not automated_position_exit_allowed(
        "龙虾/USDT", changed_position, {},
        "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", 100.0,
    )


def test_manual_close_is_explicit_and_other_symbols_keep_existing_policy():
    assert automated_position_exit_allowed(
        "CAP/USDT", position(), {}, "手動平倉", 99.0, is_manual=True,
    )
    assert not automated_position_exit_allowed(
        "CAP/USDT", position(), {}, "人工例外", 99.0, is_manual=True,
    )
    assert automated_position_exit_allowed(
        "DOGE/USDT", position(), {}, "觸發止損", 99.0,
    )


@pytest.mark.parametrize("account_module,class_name", [
    ("core.paper_account", "PaperAccount"),
    ("core.testnet_account", "BinanceTestnetAccount"),
])
def test_account_close_boundary_blocks_unauthorized_exit(account_module, class_name):
    import importlib

    account_type = getattr(importlib.import_module(account_module), class_name)
    account = object.__new__(account_type)
    account.positions = {"CAP/USDT": position()}
    account.position_meta = {}
    account.closing_lock = set()
    account.log = Mock()

    assert not asyncio.run(account.close_position(
        "CAP/USDT",
        99.0,
        "觸發止損 (Stop-Loss)",
    ))
    account.log.assert_called_once()


@pytest.mark.parametrize(
    "exchange_symbol,internal_symbol",
    [("CAPUSDT", "CAP/USDT"), ("LOBSTERUSDT", "LOBSTER/USDT")],
)
def test_account_circuit_breaker_does_not_software_close_restricted_symbols(
    exchange_symbol, internal_symbol,
):
    from core.testnet_account import BinanceTestnetAccount

    account = object.__new__(BinanceTestnetAccount)
    account._circuit_breaker_running = False
    account.exchange = SimpleNamespace(
        fapiPrivateV2GetPositionRisk=AsyncMock(return_value=[{
            "symbol": exchange_symbol,
            "positionAmt": "1",
        }]),
        fetch_open_orders=AsyncMock(return_value=[]),
    )
    account._raw_create_order = AsyncMock()
    account.log = Mock()
    account.save_state = Mock()

    report = asyncio.run(account._run_circuit_breaker_flatten())

    assert report["close_attempts"] == [{
        "symbol": internal_symbol,
        "qty": "1",
        "ok": False,
        "blocked": "SOFTWARE_EXIT_POLICY_PROFIT_LOCK_OR_CONFIRMED_PIVOT",
    }]
    account._raw_create_order.assert_not_awaited()
