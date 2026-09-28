"""Closed peak/trough crosses and structural stops through account boundaries."""
import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry
from core.services.entry_firewall import validate_entry_frame, validate_account_entry
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy


def frame(side='LONG'):
    stamp = int(time.time()//60)*60000-60000
    rows = [dict(timestamp=stamp-(8-i)*60000, open=100.,close=100.,high=100.3,low=99.7,
                 ma3=99.9,ma15=100.,atr=1.,kc_upper=101.,kc_middle=100.,kc_lower=99.,is_closed=True)
            for i in range(9)]
    rows[0]['low']=98.
    rows[-1].update(open=99.9,close=100.4,high=100.5,low=99.8,ma3=100.1)
    f=pd.DataFrame(rows)
    if side=='SHORT':
        old=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a]=200-old[b]
    return f


def context(f,side):
    return dict(entry_mode='CHANNEL_SWING',entry_signal_code=f'CLOSED_PEAK_TROUGH_CROSS_{side}',
                channel_confirmation_bar_id=float(f.iloc[-1].timestamp))


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_cross_passes_inside_flat_channel(side):
    f=frame(side)
    ok,code,d=evaluate_closed_entry(f,side)
    assert ok and code==f'CLOSED_PEAK_TROUGH_CROSS_{side}'
    assert d['initial_sl']==(98. if side=='LONG' else 102.)
    assert validate_entry_frame(f,side,code)==d


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['no_touch','old_touch','same_side','equality','wrong_body','pin','live','invalid_history','gap'])
def test_invalid_cross_rejected(side,fault):
    f=frame(side);sign=1 if side=='LONG' else -1
    if fault=='no_touch':f.loc[0,'low' if side=='LONG' else 'high']=100-sign*.3
    elif fault=='old_touch':
        extra=f.iloc[:1].copy();extra['timestamp']-=60000
        f.loc[0,'low' if side=='LONG' else 'high']=100-sign*.3
        f=pd.concat([extra,f],ignore_index=True)
    elif fault=='same_side':f.loc[7,'ma3']=100+sign*.1
    elif fault=='equality':f.loc[8,'ma3']=100.
    elif fault=='wrong_body':f.loc[8,'open']=float(f.loc[8,'close'])+sign*.01
    elif fault=='pin':f.loc[8,'high' if side=='LONG' else 'low']=100+sign*3
    elif fault=='live':f.loc[8,'is_closed']=False
    elif fault=='invalid_history':f.loc[0,'low']=float('nan')
    elif fault=='gap':f.loc[0,'timestamp']-=60000
    assert not evaluate_closed_entry(f,side)[0]
    with pytest.raises(ValueError):validate_entry_frame(f,side,f'CLOSED_PEAK_TROUGH_CROSS_{side}')


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['long_wick','wrong_half'])
def test_cross_rejected_by_wick_and_half_checks(side,fault):
    f=frame(side);sign=1 if side=='LONG' else -1
    if fault=='long_wick':
        f.loc[8,'high' if side=='LONG' else 'low']=float(f.loc[8,'close'])+sign*10.0
    elif fault=='wrong_half':
        span = float(f.loc[8,'high']) - float(f.loc[8,'low'])
        if side=='LONG':
            f.loc[8,'close'] = float(f.loc[8,'low']) + span*0.4
            f.loc[8,'open'] = float(f.loc[8,'low']) + span*0.3
        else:
            f.loc[8,'close'] = float(f.loc[8,'low']) + span*0.6
            f.loc[8,'open'] = float(f.loc[8,'low']) + span*0.7
    assert not evaluate_closed_entry(f,side)[0]
    with pytest.raises(ValueError):validate_entry_frame(f,side,f'CLOSED_PEAK_TROUGH_CROSS_{side}')


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_outside_rail_delegates_to_existing_breakout(side):
    f=frame(side);sign=1 if side=='LONG' else -1
    f.loc[6:8,'ma15']=[99.8,99.9,100.] if side=='LONG' else [100.2,100.1,100.]
    f.loc[8,'close']=100+sign*1.2
    f.loc[8,'high' if side=='LONG' else 'low']=100+sign*1.3
    assert evaluate_closed_entry(f,side)[1]==f'CLOSED_IGNITION_{side}'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_cooldown_and_refreshed_cross(side):
    f=frame(side);ctx=context(f,side)
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f),last_closed_at={})
    stamp=float(f.iloc[-1].timestamp)
    for elapsed in (0,60000):
        account.last_closed_at['TEST']=(stamp-elapsed)/1000
        with pytest.raises(ValueError,match='冷卻'):asyncio.run(validate_account_entry(account,'TEST',side,ctx))
    account.last_closed_at['TEST']=(stamp-120000)/1000
    assert asyncio.run(validate_account_entry(account,'TEST',side,ctx))['initial_sl']
    f.loc[8,'ma3']=100.
    with pytest.raises(ValueError):asyncio.run(validate_account_entry(account,'TEST',side,ctx))


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_paper_fill_keeps_absolute_peak_stop_and_breakeven(monkeypatch,side):
    from core.paper_account import PaperAccount
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    account=PaperAccount();account.balance=100.
    f=frame(side);account.entry_frame_provider=AsyncMock(return_value=f)
    price=float(f.iloc[-1].close);ctx=context(f,side)
    assert asyncio.run(account.open_position('TEST',side,price,10.,95.,0.,ctx['entry_signal_code'],atr=1.,leverage=2,entry_context=ctx))
    pos=account.positions['TEST'];stop=98. if side=='LONG' else 102.
    assert pos['sl']==pos['atr_sl']==pos['initial_sl']==stop
    assert account.position_meta['TEST']['sl']==stop
    assert account.trades[0]['initial_sl']==stop
    sign=1 if side=='LONG' else -1
    strategy=DualTrackExitStrategy();entry=pos['entry_price']
    assert strategy.evaluate_exit(pos,None,current_price=entry+sign*1.2) is None
    assert pos['stop_loss']==entry
    assert strategy.evaluate_exit(copy.deepcopy(pos),None,current_price=entry)=='EXIT_BREAKEVEN'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_testnet_finalize_preserves_peak_stop(monkeypatch,side):
    from core.testnet_account import BinanceTestnetAccount
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'save_state',lambda self,**kw:None)
    account=BinanceTestnetAccount(SimpleNamespace(create_order=AsyncMock(), price_to_precision=lambda symbol,price:str(price)))
    account._cancel_all_orders=AsyncMock();account.refresh=AsyncMock()
    f=frame(side);price=float(f.iloc[-1].close);stop=98. if side=='LONG' else 102.
    assert asyncio.run(account._finalize_new_position('TEST',side,price,1.,price,95.,0.,'test',1.,2,100,'sell' if side=='LONG' else 'buy','test-id',10.,entry_context=context(f,side),structural_stop=stop))
    assert account.position_meta['TEST']['sl']==stop
    assert account.trades[0]['initial_sl']==stop


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_touch_on_cross_bar_alone_is_insufficient(side):
    f=frame(side)
    f.loc[0,'low' if side=='LONG' else 'high']=99.7 if side=='LONG' else 100.3
    f.loc[8,'low' if side=='LONG' else 'high']=99. if side=='LONG' else 101.
    assert not evaluate_closed_entry(f,side)[0]


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_touch_rail_and_exact_body_ratio_are_inclusive(side):
    f=frame(side);sign=1 if side=='LONG' else -1
    f.loc[8,'close']=100+sign
    f.loc[8,'open']=100+sign*.2
    f.loc[8,['high','low']]=[101.,99.]
    assert evaluate_closed_entry(f,side)[1]==f'CLOSED_PEAK_TROUGH_CROSS_{side}'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_engine_scans_and_fills_early_cross_once(monkeypatch,side):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    account=PaperAccount();account.balance=100.
    e=object.__new__(TradingEngine);e.account=account
    f=frame(side);symbol='1000PEPE/USDT'
    e.tickers={symbol:float(f.iloc[-1].close)}
    e.fetch_klines=AsyncMock(return_value=f)
    e.strategy=SimpleNamespace(compute_indicators=lambda f:f)
    e.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    e._execution_price_is_safe=AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(e,symbol,time.time(),None,False,exit_frame=f))
    assert account.positions[symbol]['initial_sl']==(98. if side=='LONG' else 102.)
    assert account.trades[0]['reason']==f'Closed1M CLOSED_PEAK_TROUGH_CROSS_{side}'
    account.positions.clear()
    asyncio.run(process_single_symbol_runner(e,symbol,time.time(),None,False,exit_frame=f))
    assert len(account.trades)==1


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_testnet_market_chain_keeps_structural_stop(monkeypatch,side):
    from core.testnet_account import BinanceTestnetAccount
    from test_order_sizing import exchange
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'save_state',lambda self,**kw:None)
    f=frame(side);price=float(f.iloc[-1].close)
    ex=exchange();ex.price_to_precision=lambda symbol,p:str(p)
    ex.create_order=AsyncMock(return_value={'id':'mock','average':price,'status':'closed'})
    account=BinanceTestnetAccount(ex)
    account._ensure_markets=AsyncMock();account._prepare_leverage=AsyncMock()
    account._cancel_all_orders=AsyncMock();account.refresh=AsyncMock()
    account.entry_frame_provider=AsyncMock(return_value=f)
    assert asyncio.run(account.open_position('TEST',side,price,100.,95.,0.,'cross',atr=1.,leverage=2,signal_score=100,entry_context=context(f,side)))
    assert account.position_meta['TEST']['initial_sl']==(98. if side=='LONG' else 102.)
    account._raw_create_order.assert_awaited_once()


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_quote_beyond_structural_stop_rejects_before_paper_fill(monkeypatch,side):
    from core.paper_account import PaperAccount
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    a=PaperAccount();a.balance=100.
    f=frame(side);a.entry_frame_provider=AsyncMock(return_value=f)
    assert not asyncio.run(a.open_position('TEST',side,97. if side=='LONG' else 103.,10.,95.,0.,'cross',atr=1.,leverage=2,entry_context=context(f,side)))
    assert a.balance==100. and not a.positions
