"""Concurrency and input evidence tests without live state/network."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from test_entry_e2e_20260928 import candles
from core.engine import TradingEngine
from core.paper_account import PaperAccount
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry


def engine_fixture(monkeypatch):
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    account=PaperAccount();account.balance=100.
    engine=object.__new__(TradingEngine);engine.account=account
    f=candles()
    engine.tickers={'1000PEPE/USDT':100.7,'龙虾/USDT':100.7}
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.strategy=SimpleNamespace(compute_indicators=lambda f:f)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    engine._execution_price_is_safe=AsyncMock(return_value=True)
    return engine,f






def test_snapshot_evidence_excludes_forming_candle():
    import json
    import pandas as pd
    from core.services.candle_data import entry_frame_evidence
    f=candles()
    expected=entry_frame_evidence(f)
    live=f.iloc[-1].copy();live['timestamp']+=60000;live['is_closed']=False;live['close']=9999.
    f=pd.concat([f,pd.DataFrame([live])],ignore_index=True)
    assert entry_frame_evidence(f)==expected
    assert len(expected['candles'])==6
    assert json.loads(json.dumps(expected))==expected


