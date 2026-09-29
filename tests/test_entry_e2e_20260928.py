"""Regression coverage for signal -> firewall -> account -> market boundary."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.strategies.unified_entry_strategy import evaluate_closed_entry
from core.services.entry_firewall import validate_account_entry, validate_entry_frame


def candles(side='LONG', body=.2):
    stamp = int(time.time() // 60) * 60000 - 60000
    rows = [dict(timestamp=stamp-(5-i)*60000, open=100., close=100.1,
                 high=100.3, low=99.8, ma3=100.2, ma15=100., atr=1.,
                 kc_upper=100.4, kc_middle=100., kc_lower=99.6,
                 is_closed=True) for i in range(6)]
    rows[-1].update(open=100.7-body, close=100.7, high=100.72,
                    low=100.68-body, ma3=100.4)
    f=pd.DataFrame(rows)
    if side=='SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),
                    ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a]=200-original[b]
    f.attrs['timeframe_ms']=60000
    return f


def context(f, side):
    return dict(entry_mode='CHANNEL_SWING', entry_signal_code=evaluate_closed_entry(f,side)[1],
                channel_confirmation_bar_id=float(f.iloc[-1].timestamp), market_mode='CHOPPY')


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('body,rule',[(.2,'TREND_BREAKOUT'),(.55,'IGNITION'),(.59,'IGNITION')])
def test_small_breakout_and_ignition_pass_flat_narrow_channel(side,body,rule):
    f=candles(side,body)
    ok,code,d=evaluate_closed_entry(f,side)
    assert ok and code==f'CLOSED_{rule}_{side}'
    assert validate_entry_frame(f,side,code)==d
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f),last_closed_at={})
    assert asyncio.run(validate_account_entry(account,'TEST',side,context(f,side)))==d


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_tick_does_not_overwrite_closed_signal(side):
    f=candles(side)
    expected=evaluate_closed_entry(f,side)
    live=f.iloc[-1].copy();live['timestamp']+=60000;live['is_closed']=False
    f=pd.concat([f,pd.DataFrame([live])],ignore_index=True)
    for price in [1.,199.,100.]:
        f.loc[6,['close','ma3','kc_middle']]=price
        assert evaluate_closed_entry(f,side)==expected


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_breakout_requires_five_previous_bars(side):
    assert not evaluate_closed_entry(candles(side).iloc[-5:],side)[0]


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('body',[.2,.55])
def test_real_exchange_wrapper_reaches_market_without_extra_body_gate(monkeypatch,side,body):
    from core.testnet_account import BinanceTestnetAccount
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'save_state',lambda self, **kwargs:None)
    raw=AsyncMock(return_value={'id':'test-order','status':'closed'})
    account=BinanceTestnetAccount(SimpleNamespace(create_order=raw))
    f=candles(side,body)
    account.entry_frame_provider=AsyncMock(return_value=f)
    account.last_closed_at={}
    asyncio.run(account._send_order('TEST','market','buy' if side=='LONG' else 'sell',1,
                                   entry_context=context(f,side)))
    raw.assert_awaited_once()
    assert '_entry_context' not in raw.call_args.args[-1]
    assert any('[ORDER_ACK]' in item['text'] for item in account.logs)
    raw.reset_mock()
    with pytest.raises(ValueError,match='FORBIDDEN_ENTRY'):
        asyncio.run(account.exchange.create_order('TEST','market','buy',1))
    raw.assert_not_awaited()


@pytest.mark.parametrize('fault',['cooldown','stale','changed'])
def test_account_rejects_with_concrete_reason(fault):
    f=candles();ctx=context(f,'LONG')
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f),last_closed_at={})
    if fault=='cooldown':account.last_closed_at['TEST']=time.time()
    if fault=='stale':f['timestamp']-=300000;ctx['channel_confirmation_bar_id']-=300000
    if fault=='changed':ctx['channel_confirmation_bar_id']-=60000
    with pytest.raises(ValueError,match='FORBIDDEN_ENTRY'):
        asyncio.run(validate_account_entry(account,'TEST','LONG',ctx))


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('body',[.2,.55])
def test_paper_account_actual_fill(monkeypatch,side,body):
    from core.paper_account import PaperAccount
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    account=PaperAccount();account.balance=100.
    f=candles(side,body);account.entry_frame_provider=AsyncMock(return_value=f)
    ctx=context(f,side)
    opened=asyncio.run(account.open_position('TEST',side,float(f.iloc[-1].close),10.,
        98. if side=='LONG' else 102.,0.,ctx['entry_signal_code'],atr=1.,leverage=2,entry_context=ctx))
    assert opened and account.positions['TEST']['side']==side
    assert account.trades[-1]['action']=='OPEN_'+side


def test_exchange_reject_is_not_silent(monkeypatch):
    from core.testnet_account import BinanceTestnetAccount
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'save_state',lambda self, **kwargs:None)
    raw=AsyncMock(side_effect=RuntimeError('exchange -1013 invalid quantity'))
    account=BinanceTestnetAccount(SimpleNamespace(create_order=raw))
    f=candles();account.entry_frame_provider=AsyncMock(return_value=f)
    with pytest.raises(RuntimeError,match='-1013'):
        asyncio.run(account._send_order('TEST','market','buy',1,entry_context=context(f,'LONG')))
    assert any('[ORDER_REJECT]' in item['text'] and '-1013' in item['text'] for item in account.logs)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_runner_through_engine_to_paper_fill_and_dedup(monkeypatch,side):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    account=PaperAccount();account.balance=100.
    engine=object.__new__(TradingEngine)
    engine.account=account
    symbol='1000PEPE/USDT'
    f=candles(side)
    engine.tickers={symbol:float(f.iloc[-1].close)}
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.strategy=SimpleNamespace(compute_indicators=lambda frame:frame)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    engine._execution_price_is_safe=AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    assert account.positions[symbol]['side']==side
    assert engine._entry_gate_diagnostics[(symbol,side,'EXECUTION')][1]=='FILLED'
    assert len(account.trades)==1
    account.positions.clear()  # persisted fill still forbids replay of the same candle
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    assert len(account.trades)==1
    assert 'already filled' in engine._entry_gate_diagnostics[(symbol,side,'EXECUTION')][1]


@pytest.mark.parametrize('fault',['slots','daily','balance','quote','cooldown','api_error'])
def test_engine_exposes_real_block_instead_of_signal_only(monkeypatch,fault):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    account=PaperAccount();account.balance=100.
    engine=object.__new__(TradingEngine);engine.account=account
    symbol='1000PEPE/USDT';f=candles()
    engine.tickers={symbol:float(f.iloc[-1].close)}
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.strategy=SimpleNamespace(compute_indicators=lambda frame:frame)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    engine._execution_price_is_safe=AsyncMock(return_value=fault!='quote')
    if fault=='slots':
        import core.engine as module
        monkeypatch.setattr(module,'MAX_SLOTS',1)
        account.positions['OTHER']={}
    if fault=='daily':account.daily_loss_limit_hit=lambda:(True,10.)
    if fault=='balance':account.balance=0.
    if fault=='cooldown':account.last_closed_at[symbol]=time.time()
    if fault=='api_error':account.open_position=AsyncMock(side_effect=RuntimeError('exchange -2010 rejection'))
    code=evaluate_closed_entry(f,'LONG')[1]
    assert not asyncio.run(engine._execute_confirmed_channel_break(symbol,f,100.7,'LONG',v8_reason=code))
    reason=engine._entry_gate_diagnostics[(symbol,'LONG','EXECUTION')][1]
    expected={'slots':'MAX_SLOTS','daily':'daily loss','balance':'INSUFFICIENT_MARGIN',
              'quote':'execution_price','cooldown':'冷卻','api_error':'-2010'}
    assert expected[fault] in reason
    assert symbol not in account.positions


def test_ui_selects_execution_only_for_same_signal_bar():
    import ast
    from pathlib import Path
    module=ast.parse(Path('services/api.py').read_text())
    function=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='latest_entry_diagnostic')
    namespace={}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'diagnostic','exec'),namespace)
    choose=namespace['latest_entry_diagnostic']
    cache={('TEST','LONG','CLOSED_SIGNAL'):(1,'CLOSED_IGNITION_LONG'),
           ('TEST','LONG','EXECUTION'):(1,'ORDER_REJECT')}
    assert choose(cache,'TEST','LONG')==(1,'ORDER_REJECT')
    cache[('TEST','LONG','CLOSED_SIGNAL')]=(2,'WAIT_NEW')
    assert choose(cache,'TEST','LONG')==(2,'WAIT_NEW')


def test_blocked_word_is_not_misclassified_as_ck():
    import ast
    from pathlib import Path
    module=ast.parse(Path('services/api.py').read_text())
    function=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='map_block_reason')
    namespace={}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'reason_map','exec'),namespace)
    translate=namespace['map_block_reason']
    assert 'CK' not in translate('BLOCKED_OPPOSITE_CLOSED_BODY')
    assert translate('BLOCKED_INSUFFICIENT_MARGIN')=='BLOCKED_INSUFFICIENT_MARGIN'
    assert 'BTC' in translate('BLOCKED_BTC_DUMPING_FORBID_LONG')
