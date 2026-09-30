"""Shared current entry policy through isolated paper execution."""
import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import time

import pandas as pd
import pytest

from core.services.strategies.pure_trend_v2 import evaluate_v2_frame


def frame(side='LONG'):
    stamp = int(time.time() // 60) * 60000
    rows = [dict(timestamp=stamp-(20-i)*60000, open=100., close=100.1,
                 high=100.2, low=99.8, ma3=100.3, ma15=100., atr=1.,
                 kc_upper=101., kc_middle=100., kc_lower=99., is_closed=True)
            for i in range(21)]
    rows[-2].update(open=100.8, close=101.1, high=101.15, low=100.7, kc_middle=100.1)
    # Opposite live color, tiny body, shallow first breakout: formerly vetoed at execution.
    rows[-1].update(open=101.25, close=101.2, high=101.5, low=100.9, is_closed=False)
    f = pd.DataFrame(rows)
    if side == 'SHORT':
        original = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),
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
    symbol = '1000PEPE/USDT'; f = frame(side)
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
@pytest.mark.parametrize('fault,reason', [('freshness','整理不足'), ('extreme','第二根尚未'),
    ('spread','均線間距不足'), ('distance','0.8 ATR'), ('cooldown','5_BAR')])
def test_actual_rejection_and_diagnostics_reset(side, fault, reason):
    f = frame(side); account = None
    if fault == 'freshness':
        f.loc[f.index[-5:-2], 'close'] = 102. if side == 'LONG' else 98.
        f.loc[f.index[-5:-2], 'high' if side == 'LONG' else 'low'] = 102.1 if side == 'LONG' else 97.9
    if fault == 'extreme': f.loc[f.index[-1], 'close'] = 101.14 if side == 'LONG' else 98.86
    if fault == 'spread': f.loc[f.index[-1], 'ma3'] = f.iloc[-1].ma15
    if fault == 'distance':
        f.loc[f.index[-1], 'close'] = 102. if side == 'LONG' else 98.
        f.loc[f.index[-1], 'high' if side == 'LONG' else 'low'] = 102.1 if side == 'LONG' else 97.9
    if fault == 'cooldown':
        account = SimpleNamespace(trades=[dict(symbol='1000PEPE/USDT',action='CLOSE_'+side,
            id=float(f.iloc[-1].timestamp)-60000+1000)])
    d = {'reason':'stale'}
    assert evaluate_v2_frame(f, account=account, symbol='1000PEPE/USDT', diagnostics=d) is None
    assert reason in d['reason']
    assert evaluate_v2_frame(frame(side), symbol='1000PEPE/USDT', diagnostics=d)
    assert d['reason'] == '符合標準開倉範例'


def test_api_does_not_invent_distance_rejection():
    tree = ast.parse(Path('services/api.py').read_text())
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'map_block_reason')
    ns = {}
    exec(compile(ast.Module(body=[func], type_ignores=[]), 'services/api.py', 'exec'), ns)
    assert ns['map_block_reason']('WAIT_PURE_TREND_V2', 110., {'ma15':100., 'atr':1.}) == 'WAIT_PURE_TREND_V2'
