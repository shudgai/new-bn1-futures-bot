"""Closing is single-flight; successful fills survive stale/failed refreshes."""
import asyncio
from unittest.mock import AsyncMock
import pytest
from test_testnet_account import FakeTestnetExchange
import core.testnet_account as tm
import core.paper_account as pm


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def account_for(side, tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(tm, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tm, "notify_email", lambda *a, **k: None)
    monkeypatch.setattr(tm.BinanceTestnetAccount, "credentials_configured", staticmethod(lambda: True))
    ex = FakeTestnetExchange()
    account = tm.BinanceTestnetAccount(ex)
    await account.initialize()
    assert await account.open_position("DOGE/USDT", side, 100., 25., 0., 0., "pivot", leverage=1,
                                       entry_context={"entry_mode": "CHANNEL_SWING"})
    return account, ex


def closes(account):
    return [t for t in account.trades if t["action"].startswith("CLOSE_")]


def close_orders(ex):
    return [o for o in ex.orders if o["type"] == "market" and o["params"].get("reduceOnly")]












