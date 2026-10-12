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
                 high=100.2, low=99.8, ma3=100.1 + i * .02,
                 ma5=100.2 + i * .02, ma15=100. + i * .015, atr=1.,
                 kc_upper=100.6 + i * .02, kc_middle=99.6 + i * .02,
                 kc_lower=98.6 + i * .02, is_closed=True)
            for i in range(21)]
    rows[-3].update(open=100.7, close=101.0, high=101.1, low=100.65,
                    kc_upper=100.94, kc_middle=99.94, kc_lower=98.94)
    rows[-2].update(open=100.8, close=101.1, high=101.15, low=100.7,
                    kc_upper=100.98, kc_middle=100.1, kc_lower=99.0)
    # Healthy advancing body; shallow first breakout remains permitted.
    rows[-1].update(open=100.65, close=101.2, high=101.5, low=100.6,
                    ma3=100.7, ma5=100.7, ma15=100.3,
                    kc_upper=101., kc_middle=100., kc_lower=99.,
                    is_closed=False)
    f = pd.DataFrame(rows)
    if side == 'SHORT':
        original = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma5','ma5'),('ma15','ma15'),('kc_upper','kc_lower'),
                    ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a] = 200-original[b]
    return f


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_shared_signal_reaches_order_executor(monkeypatch, side):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    from test_intrabar_midline_entry import make_frame as midline_frame
    monkeypatch.setattr(PaperAccount, 'load_state', lambda self: None)
    monkeypatch.setattr(PaperAccount, 'save_state', lambda self: None)
    account = PaperAccount(); account.balance = 100.
    engine = object.__new__(TradingEngine); engine.account = account
    symbol = '1000PEPE/USDT'; f, quote = midline_frame(side)
    current_bar = int(time.time() * 1000 // 60_000 * 60_000)
    f['timestamp'] += current_bar - int(f.iloc[-1]['timestamp'])
    engine.tickers = {symbol: quote}
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.strategy = SimpleNamespace(compute_indicators=lambda data: data)
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args: 2)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    engine._execute_confirmed_channel_break = AsyncMock()
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    engine._execute_confirmed_channel_break.assert_awaited_once()
    assert engine._execute_confirmed_channel_break.await_args.args[3] == side


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('fault,reason', [
    ('first_break', '入口資料、棒次或訊號不符'),
    ('outer_boundary', '現價在通道內部'),
    ('spread', '均線間距不足0.08%'),
    ('distance', '距MA15超過乖離上限'),
    ('same_bar_exit', '入口資料、棒次或訊號不符'),
])
def test_actual_rejection_and_diagnostics_reset(side, fault, reason):
    f = frame(side); account = None; symbol = '1000PEPE/USDT'
    sign = 1 if side == 'LONG' else -1
    if fault == 'first_break':
        f.loc[f.index[-3], 'close'] = (
            f.iloc[-3].kc_middle - sign * .01
        )
    if fault == 'outer_boundary':
        f.loc[f.index[-1], 'close'] = (
            f.iloc[-1].kc_upper if side == 'LONG' else f.iloc[-1].kc_lower
        )
    if fault == 'spread': f.loc[f.index[-1], 'ma3'] = f.iloc[-1].ma15
    if fault == 'distance':
        symbol = 'BTC/USDT'
        price = 104. if side == 'LONG' else 96.
        f.loc[f.index[-1], 'close'] = price
        f.loc[f.index[-1], 'high' if side == 'LONG' else 'low'] = price
    if fault == 'same_bar_exit':
        account = SimpleNamespace(trades=[dict(
            symbol=symbol, action='CLOSE_'+side,
            id=float(f.iloc[-2].timestamp) + 1000,
        )])
    d = {'reason':'stale'}
    assert evaluate_v2_frame(f, account=account, symbol=symbol, diagnostics=d) is None
    assert reason in d['reason']
    assert evaluate_v2_frame(frame(side), symbol='1000PEPE/USDT', diagnostics=d)
    assert d['reason'] == '順勢延續開倉(Re-entry)'


def test_api_does_not_invent_distance_rejection():
    tree = ast.parse(Path('services/api.py').read_text())
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'map_block_reason')
    ns = {}
    exec(compile(ast.Module(body=[func], type_ignores=[]), 'services/api.py', 'exec'), ns)
    assert ns['map_block_reason']('WAIT_PURE_TREND_V2', 110., {'ma15':100., 'atr':1.}) == 'WAIT_PURE_TREND_V2'
