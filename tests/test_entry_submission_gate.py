import asyncio

import pytest

from core.paper_account import PaperAccount
from core.testnet_account import BinanceTestnetAccount


@pytest.mark.parametrize("account_type", [PaperAccount, BinanceTestnetAccount])
@pytest.mark.parametrize("method_name", ["open_position", "place_limit_entry"])
def test_disabled_entry_submission_blocks_all_account_entry_paths(
    monkeypatch, account_type, method_name,
):
    from core import config

    monkeypatch.setattr(config, "ENTRY_SUBMISSION_ENABLED", False)
    account = account_type.__new__(account_type)
    account.logs = []
    account.log = lambda message, level: account.logs.append((message, level))

    method = getattr(account, method_name)
    if method_name == "open_position":
        result = asyncio.run(method(
            "LOBSTER/USDT", "LONG", 100.0, 10.0, 0.0, 0.0, "test",
        ))
    else:
        result = asyncio.run(method(
            "LOBSTER/USDT", "LONG", 100.0, 10.0, 0.0, 0.0, "test",
        ))

    assert result is False
    assert account.logs == [(
        "ENTRY_GATE LOBSTER/USDT BLOCKED_ENTRY_SUBMISSION_DISABLED",
        "WARNING",
    )]


@pytest.mark.parametrize("account_type", [PaperAccount, BinanceTestnetAccount])
def test_disabled_entry_submission_cancels_existing_entry_orders(
    monkeypatch, account_type,
):
    from core import config

    monkeypatch.setattr(config, "ENTRY_SUBMISSION_ENABLED", False)
    account = account_type.__new__(account_type)
    account.pending_limit_orders = {"CAP/USDT": {"order_id": "pending"}}
    canceled = []

    async def cancel_pending_limit(symbol, reason):
        canceled.append((symbol, reason))
        account.pending_limit_orders.pop(symbol)

    account.cancel_pending_limit = cancel_pending_limit
    asyncio.run(account.check_pending_limit_orders())

    assert canceled == [("CAP/USDT", "ENTRY_SUBMISSION_DISABLED")]
    assert account.pending_limit_orders == {}
