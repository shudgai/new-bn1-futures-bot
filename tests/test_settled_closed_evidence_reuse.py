import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from test_lobster_cap_gates import frame
from core.services.entry_finality import fetch_settled_entry_frame

@pytest.mark.parametrize('fault',[None,'closed_change','expired','live_open_change','next_bar'])
def test_only_identical_recent_closed_evidence_avoids_repeated_wait(monkeypatch,fault):
    import core.services.entry_finality as module
    monkeypatch.setattr(module,'READ_INTERVAL_SECONDS',0)
    f=frame();e=SimpleNamespace(fetch_klines=AsyncMock(return_value=f),exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=361000.)))
    # Settled boundary requires three seconds beyond close.
    e.exchange.fetch_time.return_value=364000.
    first=asyncio.run(fetch_settled_entry_frame(e,'CAP/USDT'))
    assert first is not None
    e.fetch_klines.reset_mock();e.exchange.fetch_time.return_value=365000.
    fresh=f.copy();fresh.loc[5,'close']=101.7;fresh.loc[5,'high']=101.7
    if fault=='closed_change':fresh.loc[4,'close']+=.01
    elif fault=='expired':e.exchange.fetch_time.return_value=370000.
    elif fault=='live_open_change':fresh.loc[5,'open']+=.01
    elif fault=='next_bar':fresh.timestamp+=60000;e.exchange.fetch_time.return_value=425000.
    e.fetch_klines.return_value=fresh
    result=asyncio.run(fetch_settled_entry_frame(e,'CAP/USDT'))
    assert result is not None
    assert result.iloc[-1].close==101.7
    assert e.fetch_klines.await_count==(1 if fault is None else 2)
    if fault is None:
        assert e._settled_closed_entry_evidence['CAP/USDT'].attrs['entry_finality_server_ms']==364000.
        assert result.attrs['entry_finality_server_ms']==365000.


def test_reused_evidence_still_rejects_live_quote_rollback(monkeypatch):
    import core.services.entry_finality as module
    from core.services.entry_contract import evaluate_entry_contract
    monkeypatch.setattr(module,'READ_INTERVAL_SECONDS',0)
    f=frame();e=SimpleNamespace(fetch_klines=AsyncMock(return_value=f),exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=364000.)))
    assert asyncio.run(fetch_settled_entry_frame(e,'CAP/USDT')) is not None
    fresh=f.copy();fresh.loc[5,'close']=fresh.loc[5,'open']
    e.fetch_klines.return_value=fresh;e.exchange.fetch_time.return_value=365000.
    result=asyncio.run(fetch_settled_entry_frame(e,'CAP/USDT'))
    assert result.attrs['entry_finality_closed_reused']
    assert evaluate_entry_contract(result,code='KC_LIVE_BODY_BREAKOUT_LONG') is None
