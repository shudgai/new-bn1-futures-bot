"""KC pending lifecycle, symmetrical confirmation and isolated execution boundaries."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry


def frame_for(side='LONG', third='push', fourth=None):
    # First: actual body cross. Second: outside confirmation. Third/fourth: lifecycle.
    rows = [dict(open=100.5, close=100.7, high=100.8, low=100.4, ma5=100.5),
            dict(open=100.9, close=101.2, high=101.3, low=100.8, ma5=100.7),
            dict(open=101.2, close=101.3, high=101.4, low=101.1, ma5=100.9)]
    variants = dict(push=(101.3,101.4,101.1), small=(101.4,101.2,100.8),
                    inside=(101.3,100.9,100.8), touch=(101.3,101.,100.8),
                    large=(101.9,101.2,100.8), doji=(101.2,101.2,100.9),
                    chase=(101.3,101.6,101.1), boundary=(101.3,101.5,101.1))
    for kind in (third, fourth):
        if kind is None: continue
        o,c,ma=variants[kind]
        rows.append(dict(open=o,close=c,high=max(o,c)+.01,low=min(o,c)-.01,ma5=ma))
    for row in rows:
        row.update(atr=1.,kc_upper=101.,kc_middle=100.,kc_lower=99.,ma15=100.,ma3=row['ma5'],is_closed=True)
    rows.append(dict(rows[-1], open=101.4,close=101.4,high=101.5,low=101.3,is_closed=False))
    stamp=int(time.time()//60)*60000
    for i,row in enumerate(rows): row['timestamp']=stamp-(len(rows)-1-i)*60000
    f=pd.DataFrame(rows)
    if side=='SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma5','ma5'),('ma15','ma15'),('ma3','ma3'),('kc_upper','kc_lower'),
                    ('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a]=200-original[b]
    f.attrs.update(timeframe_ms=60000,entry_finality_verified=True)
    return f


def reason(f, **kwargs):
    d={}; result=evaluate_entry_contract(f,diagnostics=d,**kwargs)
    return result,d['reason']


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_two_closed_candles_only_create_pending(side):
    f=frame_for(side,third=None)
    d,r=reason(f)
    assert d is None and r==f'KC_BREAKOUT_{side}_PENDING'


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('third,fourth,expected',[
    ('push',None,'KC_3BAR_CONFIRM'),('small',None,'PENDING'),
    ('small','push','KC_3BAR_CONFIRM'),('inside','push','CANCELLED_INSIDE_RAIL'),
    ('touch',None,'CANCELLED_INSIDE_RAIL'),('large',None,'CANCELLED_LARGE_PULLBACK'),
    ('chase','push','CANCELLED_CHASE'),('small','small','EXPIRED'),
    ('doji','push','KC_3BAR_CONFIRM'),('boundary',None,'KC_3BAR_CONFIRM'),
])
def test_lifecycle(side,third,fourth,expected):
    f=frame_for(side,third,fourth)
    d,r=reason(f)
    assert expected in r
    assert bool(d)==(expected=='KC_3BAR_CONFIRM')
    if d:
        assert d['type']=='KC_3BAR_CONFIRM_'+side
        assert d['breakout_bar_id']==f.iloc[1].timestamp
        assert d['pair_confirmation_bar_id']==f.iloc[2].timestamp
        assert d['confirmation_bar_id']==f.iloc[-2].timestamp
        assert d['pending_wait_bars']==(2 if fourth else 1)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_expired_signal_never_renews_on_later_same_color(side):
    f=frame_for(side,'small','small')
    closed=f.iloc[:-1].copy()
    last=closed.iloc[-1].copy()
    sign=1 if side=='LONG' else -1
    last['timestamp']+=60000
    last['open']=100+sign*1.2;last['close']=100+sign*1.4
    last['high']=max(last['open'],last['close'])+.01
    last['low']=min(last['open'],last['close'])-.01
    last['ma5']=100+sign*1.1
    closed=pd.concat([closed,last.to_frame().T],ignore_index=True)
    closed['is_closed']=True
    assert evaluate_entry_contract(closed) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['first_gap','first_doji','second_reverse','second_inside','second_ma_flat','second_ma_alignment','confirm_ma_slope','confirm_ma_alignment','missing_ma','bad_atr','gap','future_order'])
def test_required_structure(side,fault):
    f=frame_for(side);sign=1 if side=='LONG' else -1
    if fault=='first_gap': f.loc[1,'open']=100+sign*1.1
    elif fault=='first_doji': f.loc[1,'open']=f.loc[1,'close']
    elif fault=='second_reverse':
        f.loc[2,'open']=100+sign*1.35
    elif fault=='second_inside': f.loc[2,'kc_upper' if side=='LONG' else 'kc_lower']=f.loc[2,'close']
    elif fault=='second_ma_flat': f.loc[2,'ma5']=f.loc[1,'ma5']
    elif fault=='second_ma_alignment': f.loc[2,'ma15']=f.loc[2,'ma5']
    elif fault=='confirm_ma_slope': f.loc[3,'ma5']=f.loc[2,'ma5']
    elif fault=='confirm_ma_alignment': f.loc[3,'ma15']=f.loc[3,'ma5']
    elif fault=='missing_ma': f=f.drop(columns='ma5')
    elif fault=='bad_atr': f.loc[3,'atr']=float('nan')
    elif fault=='gap': f.loc[2,'timestamp']-=1000
    elif fault=='future_order': f.loc[1,'is_closed']=False
    assert evaluate_entry_contract(f) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_candle_never_confirms_or_cancels_closed_pending(side):
    f=frame_for(side,'small')
    # Third is forming; only the first two count, whatever the live quote does.
    f=f.iloc[:-1].copy();f.loc[f.index[-1],'is_closed']=False
    for quote in (90.,110.):
        d,r=reason(f,price=quote)
        assert d is None and r==f'KC_BREAKOUT_{side}_PENDING'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_repeated_read_only_checks_and_successful_fill_dedup(side):
    f=frame_for(side,'small','push');before=f.copy(deep=True)
    first=evaluate_entry_contract(f)
    assert first==evaluate_entry_contract(f)
    pd.testing.assert_frame_equal(before,f)
    account=SimpleNamespace(positions={},trades=[dict(symbol='TEST',action='OPEN_'+side,
        entry_snapshot={'pending_signal_id':first['pending_signal_id']})])
    d,r=reason(f,account=account,symbol='TEST')
    assert d is None and r=='BLOCKED_KC_BREAKOUT_ALREADY_FILLED'


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['old_code','unsettled','changed_seed','changed_bar','inside','same_close','position'])
def test_account_revalidation(side,fault):
    f=frame_for(side);d=evaluate_entry_contract(f)
    ctx=dict(entry_signal_code=d['type'],channel_confirmation_bar_id=d['confirmation_bar_id'],
             entry_snapshot={'pending_signal_id':d['pending_signal_id']})
    account=SimpleNamespace(positions={},trades=[],entry_frame_provider=AsyncMock(return_value=f))
    if fault=='old_code':ctx['entry_signal_code']='KC_2BAR_BREAKOUT_'+side
    elif fault=='unsettled':f.attrs['entry_finality_verified']=False
    elif fault=='changed_seed':ctx['entry_snapshot']['pending_signal_id']='wrong'
    elif fault=='changed_bar':ctx['channel_confirmation_bar_id']-=60000
    elif fault=='inside':f.loc[3,'kc_upper' if side=='LONG' else 'kc_lower']=f.loc[3,'close']
    elif fault=='same_close':account.trades=[dict(symbol='TEST',action='CLOSE_'+side,id=float(f.iloc[-1].timestamp)+1)]
    elif fault=='position':account.positions={'TEST':{}}
    with pytest.raises(ValueError):asyncio.run(validate_account_entry(account,'TEST',side,ctx))


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_real_engine_only_fills_after_structure_and_dedups(monkeypatch,side):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS',0.)
    account=PaperAccount();account.balance=100.
    engine=object.__new__(TradingEngine);engine.account=account
    symbol='1000PEPE/USDT';f=frame_for(side,'small','push')
    engine.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=float(f.iloc[-1].timestamp)+10000))
    engine.tickers={symbol:float(f.iloc[-1].close)}
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.strategy=SimpleNamespace(compute_indicators=lambda x:x)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *a:2)
    engine._execution_price_is_safe=AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    assert account.positions[symbol]['side']==side
    assert account.trades[0]['entry_snapshot']['pending_wait_bars']==2
    account.positions.clear()
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    assert len(account.trades)==1


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('body,expected',[(.5,'PENDING'),(.5001,'CANCELLED_LARGE_PULLBACK')])
def test_small_pullback_exact_boundary(side,body,expected):
    f=frame_for(side,'small');sign=1 if side=='LONG' else -1
    f.loc[3,'open']=float(f.loc[3,'close'])+sign*body
    f.loc[3,'high']=max(f.loc[3,'open'],f.loc[3,'close'])+.01
    f.loc[3,'low']=min(f.loc[3,'open'],f.loc[3,'close'])-.01
    assert expected in reason(f)[1]


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_fresh_breakout_required_and_can_recover_after_cancel(side):
    f=frame_for(side,'inside').iloc[:-1].copy()
    fresh=frame_for(side).iloc[1:-1].copy()
    start=float(f.iloc[-1].timestamp)+60000
    fresh['timestamp']=[start+i*60000 for i in range(len(fresh))]
    both=pd.concat([f,fresh],ignore_index=True)
    both.attrs.update(timeframe_ms=60000)
    d=evaluate_entry_contract(both)
    assert d and d['breakout_bar_id']==start


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['daily','balance','quote','slots'])
def test_pending_confirmation_keeps_engine_risk_gates(monkeypatch,side,fault):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS',0.)
    account=PaperAccount();account.balance=100.
    engine=object.__new__(TradingEngine);engine.account=account
    f=frame_for(side);d=evaluate_entry_contract(f);symbol='1000PEPE/USDT'
    engine.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=float(f.iloc[-1].timestamp)+10000))
    engine.tickers={symbol:float(f.iloc[-1].close)}
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.strategy=SimpleNamespace(compute_indicators=lambda x:x)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *a:2)
    engine._execution_price_is_safe=AsyncMock(return_value=fault!='quote')
    if fault=='daily':account.daily_loss_limit_hit=lambda:(True,10.)
    elif fault=='balance':account.balance=0.
    elif fault=='slots':
        monkeypatch.setattr('core.engine.MAX_SLOTS',1)
        account.positions['OTHER']={}
    assert not asyncio.run(engine._execute_confirmed_channel_break(symbol,f,float(f.iloc[-1].close),side,
        v8_reason=d['type'],candidate_bar_id=d['confirmation_bar_id']))
    assert not account.trades
