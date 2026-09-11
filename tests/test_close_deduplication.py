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


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_concurrent_testnet_closes_submit_once(side, tmp_path, monkeypatch):
    a, ex = await account_for(side, tmp_path, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    create = ex.create_order
    async def delayed(*args, **kwargs):
        started.set(); await release.wait()
        return await create(*args, **kwargs)
    ex.create_order = delayed
    first = asyncio.create_task(a.close_position("DOGE/USDT", 100., "Channel Swing middle", True))
    await started.wait()
    results = await asyncio.gather(*(a.close_position("DOGE/USDT", 100., "Channel Swing profit", True) for _ in range(10)))
    assert results == [False] * 10
    release.set(); assert await first
    assert len(close_orders(ex)) == len(closes(a)) == 1
    assert not a.closing_lock


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_successful_close_is_not_failed_by_refresh(side, tmp_path, monkeypatch):
    a, ex = await account_for(side, tmp_path, monkeypatch)
    a.refresh = AsyncMock(side_effect=RuntimeError("refresh unavailable"))
    assert await a.close_position("DOGE/USDT", 100., "Channel Swing middle", True)
    assert not await a.close_position("DOGE/USDT", 100., "Channel Swing middle", True)
    assert len(close_orders(ex)) == len(closes(a)) == 1
    assert "DOGE/USDT" not in a._close_retry_after


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_stale_refresh_cannot_resurrect_just_closed_position(side, tmp_path, monkeypatch):
    a, ex = await account_for(side, tmp_path, monkeypatch)
    stale = list(ex.positions)
    ex.fapiPrivateV2GetPositionRisk = AsyncMock(return_value=stale)
    assert await a.close_position("DOGE/USDT", 100., "Channel Swing middle", True)
    assert "DOGE/USDT" not in a.positions
    assert not await a.close_position("DOGE/USDT", 100., "Channel Swing middle", True)
    assert len(close_orders(ex)) == len(closes(a)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_inflight_refresh_from_before_close_is_discarded(side, tmp_path, monkeypatch):
    a, ex = await account_for(side, tmp_path, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    fetch = ex.fapiPrivateV2GetPositionRisk
    first = True
    async def delayed():
        nonlocal first
        if first:
            first = False; rows = await fetch(); started.set(); await release.wait(); return rows
        return await fetch()
    ex.fapiPrivateV2GetPositionRisk = delayed
    refreshing = asyncio.create_task(a.refresh(force=True))
    await started.wait()
    assert await a.close_position("DOGE/USDT", 100., "Channel Swing middle", True)
    release.set(); await refreshing
    assert "DOGE/USDT" not in a.positions
    assert len(closes(a)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_strategy_failure_cooldown_keeps_manual_override(side, tmp_path, monkeypatch):
    a, ex = await account_for(side, tmp_path, monkeypatch)
    create = ex.create_order
    ex.create_order = AsyncMock(side_effect=RuntimeError("order rejected"))
    assert not await a.close_position("DOGE/USDT", 100., "Channel Swing middle", True)
    assert "DOGE/USDT" in a.positions and not closes(a)
    assert not await a.close_position("DOGE/USDT", 100., "Channel Swing middle", True)
    assert ex.create_order.await_count == 1
    ex.create_order = create
    assert await a.close_position("DOGE/USDT", 100., "手動平倉", True)
    assert len(close_orders(ex)) == len(closes(a)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_concurrent_paper_closes_book_once(side, tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "STATE_FILE", str(tmp_path / "paper.json"))
    a = pm.PaperAccount()
    a.balance = a.daily_start_balance = 1000.
    assert await a.open_position("DOGE/USDT", side, 100., 25., 0., 0., "pivot", leverage=1,
                                 entry_context={"entry_mode": "CHANNEL_SWING"})
    results = await asyncio.gather(*(a.close_position("DOGE/USDT", 100., "Channel Swing middle", True) for _ in range(10)))
    assert results.count(True) == 1
    assert len(closes(a)) == 1
    balance = a.balance
    assert not await a.close_position("DOGE/USDT", 100., "Channel Swing middle", True)
    assert a.balance == balance
