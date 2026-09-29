"""Concurrency and input evidence tests without live state/network."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from test_v2_execution_boundary import candles
from core.engine import TradingEngine
from core.paper_account import PaperAccount
from core.services.strategies.pure_trend_v2 import evaluate_v2_frame


def engine_fixture(monkeypatch):
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    account=PaperAccount();account.balance=100.
    engine=object.__new__(TradingEngine);engine.account=account
    f=candles()
    engine.tickers={'1000PEPE/USDT':101.8,'龙虾/USDT':101.8}
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.strategy=SimpleNamespace(compute_indicators=lambda f:f)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    engine._execution_price_is_safe=AsyncMock(return_value=True)
    return engine,f


@pytest.mark.parametrize('parallel',[2,50,200])
def test_same_candle_concurrent_requests_fill_once(monkeypatch,parallel):
    engine,f=engine_fixture(monkeypatch)
    async def run():
        return await asyncio.gather(*(engine._execute_confirmed_channel_break(
            '1000PEPE/USDT',f,101.8,'LONG',candidate_bar_id=float(f.iloc[-1].timestamp),v8_reason=evaluate_v2_frame(f)['type']) for _ in range(parallel)))
    assert sum(asyncio.run(run()))==1
    assert len(engine.account.trades)==1


def test_two_symbols_cannot_race_past_single_slot(monkeypatch):
    import core.engine as module
    monkeypatch.setattr(module,'MAX_SLOTS',1)
    engine,f=engine_fixture(monkeypatch)
    # Isolate slot enforcement from the independent fee/balance veto.
    async def delayed(*args,**kwargs):
        await asyncio.sleep(.01)
        engine.account.positions[kwargs['symbol']]={'side':kwargs['side'], 'margin':kwargs['amount_usdt']}
        return True
    engine.account.open_position=delayed
    async def run():
        return await asyncio.gather(*(engine._execute_confirmed_channel_break(
            symbol,f,101.8,'LONG',candidate_bar_id=float(f.iloc[-1].timestamp),v8_reason=evaluate_v2_frame(f)['type'])
            for symbol in engine.tickers))
    assert sum(asyncio.run(run()))==1
    assert len(engine.account.positions)==1


def test_snapshot_evidence_excludes_forming_candle():
    import json
    import pandas as pd
    from core.services.candle_data import entry_frame_evidence
    f=candles()
    expected=entry_frame_evidence(f)
    live=f.iloc[-1].copy();live['timestamp']+=60000;live['is_closed']=False;live['close']=9999.
    f=pd.concat([f,pd.DataFrame([live])],ignore_index=True)
    assert entry_frame_evidence(f)==expected
    assert len(expected['candles'])==5
    assert json.loads(json.dumps(expected))==expected


def test_account_failure_releases_submit_lock_for_other_symbol(monkeypatch):
    engine,f=engine_fixture(monkeypatch)
    original=engine.account.open_position
    async def maybe_fail(*args,**kwargs):
        if kwargs['symbol']=='1000PEPE/USDT':raise RuntimeError('injected broker failure')
        return await original(*args,**kwargs)
    engine.account.open_position=maybe_fail
    async def run():
        code=evaluate_v2_frame(f)['type']
        results=await asyncio.gather(*(engine._execute_confirmed_channel_break(symbol,f,101.8,'LONG',candidate_bar_id=float(f.iloc[-1].timestamp),v8_reason=code)
                                      for symbol in engine.tickers))
        assert not engine._account_entry_submit_lock.locked()
        return results
    assert asyncio.run(run())==[False,True]
