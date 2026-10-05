"""Shared current entry policy through isolated paper execution."""
import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import time

import pandas as pd
import pytest

from core.services.entry_contract import evaluate_entry_contract as evaluate_v2_frame


@pytest.fixture(autouse=True)
def isolated_clock(monkeypatch):
    now=int(time.time()//60)*60+10
    monkeypatch.setattr(time,'time',lambda:now)
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS',0.)

def frame(side='LONG'):
    stamp = int(time.time() // 60) * 60000
    rows = [dict(timestamp=stamp-(20-i)*60000, open=100., close=100.1,
                 high=100.2, low=99.8, ma3=100.3, ma5=100., ma15=100., atr=1.,
                 kc_upper=101., kc_middle=100., kc_lower=99., is_closed=True)
            for i in range(21)]
    rows[-2].update(open=100.8, close=101.1, high=101.15, low=100.7, kc_middle=100.1)
    # Healthy advancing body; shallow first breakout remains permitted.
    rows[-1].update(open=100.65, close=101.2, high=101.5, low=100.6, is_closed=False)
    f = pd.DataFrame(rows)
    if side == 'SHORT':
        original = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma5','ma5'),('ma15','ma15'),('kc_upper','kc_lower'),
                    ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a] = 200-original[b]
    return f


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_shared_signal_reaches_real_paper_account(monkeypatch, side):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount, 'load_state', lambda self: None)
    monkeypatch.setattr(PaperAccount, 'save_state', lambda self: None)
    account = PaperAccount(); account.balance = 100.
    engine = object.__new__(TradingEngine); engine.account = account
    symbol = 'CAP/USDT'; f = frame(side)
    engine.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=float(f.iloc[-1].timestamp)+10000))
    engine.tickers = {symbol: float(f.iloc[-1].close)}
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.strategy = SimpleNamespace(compute_indicators=lambda data: data)
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args: 2)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    assert account.positions[symbol]['side'] == side
    assert engine._entry_gate_diagnostics[(symbol,side,'EXECUTION')][1] == 'FILLED'
    assert len(account.trades) == 1


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('fault,reason', [('atr','WAIT_VALID_ENTRY_DATA'), ('doji','WAIT_VALID_DIRECTION'),
    ('quote','WAIT_VALID_ENTRY_DATA'), ('rail','WAIT_VALID_ENTRY_DATA'), ('cooldown','WAIT_POST_EXIT_NEW_FORMATION')])
def test_actual_rejection_and_diagnostics_reset(side, fault, reason):
    f=frame(side);account=None;quote=float(f.iloc[-1].close)
    if fault=='atr':f.loc[f.index[-2],'atr']=float('nan')
    if fault=='doji':f.loc[f.index[-1],'open']=quote
    if fault=='quote':quote=float('nan')
    if fault=='rail':f.loc[f.index[-1],'kc_upper']=f.iloc[-1].kc_lower
    if fault=='cooldown':account=SimpleNamespace(trades=[],last_closed_at={'CAP/USDT':(float(f.iloc[-1].timestamp)+1000)/1000})
    d={'reason':'stale'}
    assert evaluate_v2_frame(f,quote,account=account,symbol='CAP/USDT',diagnostics=d) is None
    assert d['reason']==reason
    restored=evaluate_v2_frame(frame(side),symbol='CAP/USDT',diagnostics=d)
    assert restored and d['reason']==restored['type']


def test_api_does_not_invent_distance_rejection():
    tree = ast.parse(Path('services/api.py').read_text())
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'map_block_reason')
    ns = {}
    exec(compile(ast.Module(body=[func], type_ignores=[]), 'services/api.py', 'exec'), ns)
    assert ns['map_block_reason']('WAIT_PURE_TREND_V2', 110., {'ma15':100., 'atr':1.}) == 'WAIT_PURE_TREND_V2'
