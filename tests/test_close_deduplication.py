"""Closing is single-flight; successful fills survive stale/failed refreshes."""
import asyncio
from unittest.mock import AsyncMock
import pandas as pd
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
    qty = 0.25
    signed_qty = qty if side == "LONG" else -qty
    ex.positions = [{
        "symbol": "DOGEUSDT", "positionAmt": str(signed_qty),
        "entryPrice": "100", "markPrice": "100", "leverage": "1",
        "unRealizedProfit": "0",
    }]
    account.positions["DOGE/USDT"] = {
        "side": side, "entry_price": 100.0, "qty": qty, "margin": 25.0,
        "leverage": 1.0, "open_timestamp": 0.0,
        "entry_mode": "CHANNEL_SWING", "trend_lock_managed": True,
        "initial_sl": 95.0 if side == "LONG" else 105.0,
    }
    account.position_meta["DOGE/USDT"] = {"trend_lock_managed": True}
    current_close = 99.0 if side == "LONG" else 101.0
    frame = pd.DataFrame([
        {"timestamp": 60_000, "open": 100.0, "high": 100.5,
         "low": 99.5, "close": 100.0, "ma3": 100.0, "ma15": 100.0,
         "kc_middle": 100.0, "kc_upper": 102.0, "kc_lower": 98.0,
         "is_closed": True},
        {"timestamp": 120_000, "open": 100.0, "high": 101.5,
         "low": 98.5, "close": current_close, "ma3": 100.0, "ma15": 100.0,
         "kc_middle": 100.0, "kc_upper": 102.0, "kc_lower": 98.0,
         "is_closed": True},
    ])
    frame.attrs.update(timeframe_ms=60_000, snapshot_ms=180_000)
    account._channel_exit_frames = {"DOGE/USDT": frame}
    return account, ex


def closes(account):
    return [t for t in account.trades if t["action"].startswith("CLOSE_")]


def close_orders(ex):
    return [o for o in ex.orders if o["params"].get("reduceOnly")]


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
    first = asyncio.create_task(a.close_position("DOGE/USDT", 100., "手動平倉", True))
    await started.wait()
    results = await asyncio.gather(*(a.close_position("DOGE/USDT", 100., "手動平倉", True) for _ in range(10)))
    assert results == [False] * 10
    release.set(); assert await first
    assert len(close_orders(ex)) == len(closes(a)) == 1
    assert not a.closing_lock


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_successful_close_is_not_failed_by_refresh(side, tmp_path, monkeypatch):
    a, ex = await account_for(side, tmp_path, monkeypatch)
    a.refresh = AsyncMock(side_effect=RuntimeError("refresh unavailable"))
    assert await a.close_position("DOGE/USDT", 100., "手動平倉", True)
    assert not await a.close_position("DOGE/USDT", 100., "手動平倉", True)
    assert len(close_orders(ex)) == len(closes(a)) == 1
    assert "DOGE/USDT" not in a._close_retry_after


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_stale_refresh_cannot_resurrect_just_closed_position(side, tmp_path, monkeypatch):
    a, ex = await account_for(side, tmp_path, monkeypatch)
    stale = list(ex.positions)
    ex.fapiPrivateV2GetPositionRisk = AsyncMock(return_value=stale)
    assert await a.close_position("DOGE/USDT", 100., "手動平倉", True)
    assert "DOGE/USDT" not in a.positions
    assert not await a.close_position("DOGE/USDT", 100., "手動平倉", True)
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
    assert await a.close_position("DOGE/USDT", 100., "手動平倉", True)
    release.set(); await refreshing
    assert "DOGE/USDT" not in a.positions
    assert len(closes(a)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reason", ["Channel Swing middle", "DualTrackExit RATCHET_PROFIT_LOCK_EXIT"])
async def test_strategy_failure_cooldown_keeps_manual_override(side, reason, tmp_path, monkeypatch):
    a, ex = await account_for(side, tmp_path, monkeypatch)
    create = ex.create_order
    ex.create_order = AsyncMock(side_effect=RuntimeError("order rejected"))
    assert not await a.close_position("DOGE/USDT", 100., reason, True)
    assert "DOGE/USDT" in a.positions and not closes(a)
    assert not await a.close_position("DOGE/USDT", 100., reason, True)
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
    qty = 0.25
    a.positions["DOGE/USDT"] = {
        "side": side, "entry_price": 100.0, "qty": qty, "margin": 25.0,
        "leverage": 1.0, "open_timestamp": 0.0,
        "entry_mode": "CHANNEL_SWING", "trend_lock_managed": True,
    }
    a.position_meta["DOGE/USDT"] = {"trend_lock_managed": True}
    results = await asyncio.gather(*(a.close_position("DOGE/USDT", 100., "手動平倉", True) for _ in range(10)))
    assert results.count(True) == 1
    assert len(closes(a)) == 1
    balance = a.balance
    assert not await a.close_position("DOGE/USDT", 100., "手動平倉", True)
    assert a.balance == balance
