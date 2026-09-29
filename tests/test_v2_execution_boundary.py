"""V2 signal -> engine -> real paper account, with no external orders."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.strategies.pure_trend_v2 import evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry, validate_entry_frame


def candles(side='LONG', live=True):
    stamp = int(time.time() // 60) * 60000
    rows = [dict(timestamp=stamp-(5-i)*60000, open=100., close=100.2,
                 high=100.4, low=99.8, ma3=100.2, ma15=100., atr=1.,
                 kc_upper=101.4, kc_middle=100., kc_lower=98.6, is_closed=True)
            for i in range(6)]
    rows[-3].update(open=100.8, close=101.5, high=101.6, low=100.7)
    rows[-2].update(open=101.5, close=101.8, high=101.9, low=101.4)
    rows[-1].update(open=101.9, close=101.8, high=102.1, low=101.7, is_closed=not live)
    if not live:
        for row in rows: row['timestamp'] -= 60000
    f = pd.DataFrame(rows)
    if side == 'SHORT':
        original = f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),
                    ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a] = 200-original[b]
    f.attrs['timeframe_ms'] = 60000
    return f


def context(f, side):
    d = evaluate_v2_frame(f)
    return dict(entry_mode='CHANNEL_SWING', entry_signal_code=d['type'],
                channel_confirmation_bar_id=d['confirmation_bar_id'])


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('live', [True,False])
def test_runner_to_paper_fill_and_dedup(monkeypatch, side, live):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount, 'load_state', lambda self: None)
    monkeypatch.setattr(PaperAccount, 'save_state', lambda self: None)
    account = PaperAccount(); account.balance = 100.
    engine = object.__new__(TradingEngine); engine.account = account
    symbol = '1000PEPE/USDT'; f = candles(side, live)
    engine.tickers = {symbol: float(f.iloc[-1].close)}
    engine.fetch_klines = AsyncMock(return_value=f)
    engine.strategy = SimpleNamespace(compute_indicators=lambda frame: frame)
    engine.symbol_rotation = SimpleNamespace(get_dynamic_leverage=lambda *args: 2)
    engine._execution_price_is_safe = AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    assert account.positions[symbol]['side'] == side
    assert account.positions[symbol]['entry_atr'] == 1.
    assert engine._entry_gate_diagnostics[(symbol,side,'EXECUTION')][1] == 'FILLED'
    account.positions.clear()
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    assert len(account.trades) == 1
    assert 'already filled' in engine._entry_gate_diagnostics[(symbol,side,'EXECUTION')][1]


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('fault', ['inside','atr','wrong_side','unknown','expired','changed'])
def test_account_boundary_rejects_invalid(side, fault):
    f = candles(side); ctx = context(f,side)
    if fault == 'inside': f.loc[f.index[-1], 'close'] = 100.
    if fault == 'atr': f.loc[f.index[-2], 'atr'] = float('nan')
    if fault == 'wrong_side': side = 'SHORT' if side == 'LONG' else 'LONG'
    if fault == 'unknown': ctx['entry_signal_code'] = 'UNKNOWN'
    if fault == 'expired':
        f['timestamp'] -= 300000; ctx['channel_confirmation_bar_id'] -= 300000
    if fault == 'changed': ctx['channel_confirmation_bar_id'] -= 60000
    account = SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f), last_closed_at={})
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(account,'TEST',side,ctx))


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_exchange_boundary_sends_v2_order(monkeypatch, side):
    from core.testnet_account import BinanceTestnetAccount
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'save_state',lambda self,**kwargs:None)
    raw = AsyncMock(return_value={'id':'test-order','status':'closed'})
    account = BinanceTestnetAccount(SimpleNamespace(create_order=raw))
    f = candles(side); account.entry_frame_provider = AsyncMock(return_value=f)
    account.last_closed_at = {}
    asyncio.run(account._send_order('TEST','market','buy' if side=='LONG' else 'sell',1,
                                   entry_context=context(f,side)))
    raw.assert_awaited_once()


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_obsolete_entry_codes_rejected(side):
    f = candles(side)
    for prefix in ('THIRD_BAR_INTRA_', 'THIRD_BAR_TRACK_RIDING_', 'THREE_BAR_BREAKOUT_', 'CONTINUATION_'):
        with pytest.raises(ValueError):
            validate_entry_frame(f,side,prefix+side)


@pytest.mark.parametrize('fault', ['slots','daily','balance','quote','changed','exchange_error'])
def test_engine_risk_and_failure_gates(monkeypatch, fault):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    account=PaperAccount(); account.balance=100.
    engine=object.__new__(TradingEngine); engine.account=account
    symbol='1000PEPE/USDT'; f=candles(); ctx=context(f,'LONG')
    engine.tickers={symbol:float(f.iloc[-1].close)}
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.strategy=SimpleNamespace(compute_indicators=lambda frame:frame)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    engine._execution_price_is_safe=AsyncMock(return_value=fault!='quote')
    if fault=='slots':
        monkeypatch.setattr('core.engine.MAX_SLOTS',1)
        account.positions['OTHER']={}
    if fault=='daily': account.daily_loss_limit_hit=lambda:(True,10.)
    if fault=='balance': account.balance=0.
    if fault=='changed': ctx['channel_confirmation_bar_id']-=60000
    if fault=='exchange_error': account.open_position=AsyncMock(side_effect=RuntimeError('exchange rejected'))
    assert not asyncio.run(engine._execute_confirmed_channel_break(symbol,f,101.8,'LONG',
        v8_reason=ctx['entry_signal_code'],candidate_bar_id=ctx['channel_confirmation_bar_id']))
    reason=engine._entry_gate_diagnostics[(symbol,'LONG','EXECUTION')][1]
    assert {'slots':'MAX_SLOTS','daily':'daily loss','balance':'INSUFFICIENT_MARGIN',
            'quote':'execution_price','changed':'snapshot is None','exchange_error':'exchange rejected'}[fault] in reason
    assert symbol not in account.positions
