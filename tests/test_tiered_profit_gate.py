import asyncio
import copy
import time
from types import SimpleNamespace
import pandas as pd
import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing,STATE_KEY
from core.services.post_profit_lock_gate import post_profit_lock_reason,profit_exit_fields
from core.services.entry_contract import evaluate_entry_contract
from test_strict_entry_gates_live import frame_for


def position(side):
    return dict(symbol='CAP/USDT',side=side,entry_price=100.,qty=1.,margin=10.,
                leverage=10.,open_timestamp=60.,entry_mode='CHANNEL_SWING')


def tick(p,roi,stamp):
    sign=1 if p['side']=='LONG' else -1
    return evaluate_peak_trailing(p,100+sign*roi*10,{'quote_ms':stamp},fee=0.,slippage=0.)


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('peak,allowance,code',[(.05,.012,'PROFIT_LOCK_T1'),(.1,.025,'PROFIT_LOCK_T2'),(.15,.03,'PROFIT_LOCK_T3'),(.2,.04,'PROFIT_LOCK_T3')])
def test_tier_boundary_and_retry(side,peak,allowance,code):
    p=position(side)
    assert tick(p,peak,61000) is None
    assert tick(p,peak-allowance+.00001,62000) is None
    assert tick(p,peak-allowance,63000)['trigger']==code
    restored=copy.deepcopy(p)
    assert tick(restored,peak,64000)['trigger']==code
    event=profit_exit_fields(restored,code,65000)
    assert event['last_profit_exit_side']==side
    assert event['last_profit_exit_peak_price']==100+(1 if side=='LONG' else -1)*peak*10


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_upgrade_uses_wider_tier_and_never_arms_below_five(side):
    p=position(side)
    assert tick(p,.049,61000) is None
    assert tick(p,.01,62000) is None
    assert tick(p,.1,63000) is None
    assert tick(p,.087,64000) is None


def event_account(side,now,peak=110.):
    return SimpleNamespace(positions={},trades=[dict(symbol='X',action='CLOSE_'+side,status='CLOSED',
        last_profit_exit_timestamp=now,last_profit_exit_side=side,last_profit_exit_peak_price=peak)])


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_gate_cooldown_extreme_and_expiry(side):
    f=frame_for(side);closed=f.iloc[:-1];now=time.time()*1000
    peak=110. if side=='LONG' else 90.
    a=event_account(side,now,peak)
    assert post_profit_lock_reason(a,'X',closed,side,now+239999)=='BLOCKED_BY_POST_PROFIT_COOLDOWN'
    reason='BLOCKED_BY_PEAK_EXHAUSTION_GATE' if side=='LONG' else 'BLOCKED_BY_TROUGH_EXHAUSTION_GATE'
    assert post_profit_lock_reason(a,'X',closed,side,now+240000)==reason
    assert post_profit_lock_reason(a,'X',closed,side,now+900000) is None
    closed=closed.copy();closed.loc[closed.index[-1],'close']=peak
    assert post_profit_lock_reason(a,'X',closed,side,now+240000)==reason
    closed.loc[closed.index[-1],'close']=peak+(1 if side=='LONG' else -1)
    assert post_profit_lock_reason(a,'X',closed,side,now+240000) is None
    assert post_profit_lock_reason(a,'X',closed,'SHORT' if side=='LONG' else 'LONG',now+1) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_second_third_cannot_bypass_post_profit_gate(side):
    f=frame_for(side);a=event_account(side,time.time()*1000)
    diagnostics={}
    assert evaluate_entry_contract(f,account=a,symbol='X',diagnostics=diagnostics) is None
    assert diagnostics['reason']=='BLOCKED_BY_POST_PROFIT_COOLDOWN'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_midline_reset_requires_post_exit_cross(side):
    now=1000000.;a=event_account(side,now,110. if side=='LONG' else 90.)
    sign=1 if side=='LONG' else -1
    closed=pd.DataFrame([dict(timestamp=now-60000,close=100+sign,kc_middle=100),
                         dict(timestamp=now+60000,close=100-sign,kc_middle=100)])
    assert post_profit_lock_reason(a,'X',closed,side,now+240000) is None
    closed['timestamp']-=180000
    assert post_profit_lock_reason(a,'X',closed,side,now+240000) is not None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_actual_paper_close_persists_event_and_reload(tmp_path,monkeypatch,side):
    import core.paper_account as pm
    monkeypatch.setattr(pm,'STATE_FILE',str(tmp_path/'paper.json'))
    a=pm.PaperAccount();p=position(side);tick(p,.1,61000);tick(p,.075,62000)
    a.positions['CAP/USDT']=p;a.position_meta['CAP/USDT']={STATE_KEY:copy.deepcopy(p[STATE_KEY])}
    assert asyncio.run(a.close_position('CAP/USDT',100.75 if side=='LONG' else 99.25,'PROFIT_LOCK_T2',is_manual=True))
    b=pm.PaperAccount()
    event=b.trades[0]
    assert event['last_profit_exit_side']==side
    assert event['last_profit_exit_peak_price']==101. if side=='LONG' else event['last_profit_exit_peak_price']==99.
    assert post_profit_lock_reason(b,'CAP/USDT',frame_for(side).iloc[:-1],side)=='BLOCKED_BY_POST_PROFIT_COOLDOWN'


@pytest.mark.parametrize('order,expected',[
    ({'status':'closed','filled':1},True),
    ({'info':{'status':'FILLED','executedQty':'1'}},True),
    ({'status':'open','filled':.5},False),
    ({'status':'closed','filled':.5},False),
    ({'id':'unknown'},False),
])
def test_unconfirmed_or_partial_close_cannot_record_event(order,expected):
    from core.services.post_profit_lock_gate import confirmed_full_close
    assert confirmed_full_close(order,1)==expected


@pytest.mark.parametrize('filled',[True,False])
def test_real_testnet_close_only_records_confirmed_event(tmp_path,monkeypatch,filled):
    from unittest.mock import AsyncMock
    import core.testnet_account as tm
    monkeypatch.setattr(tm,'notify_email',lambda *a,**k:None)
    from test_testnet_account import FakeTestnetExchange
    a=tm.BinanceTestnetAccount(FakeTestnetExchange(),state_file=str(tmp_path/'testnet.json'))
    p=position('LONG');tick(p,.1,61000);tick(p,.075,62000)
    a.positions['CAP/USDT']=p
    a.position_meta['CAP/USDT']={STATE_KEY:copy.deepcopy(p[STATE_KEY])}
    a._cancel_all_orders=AsyncMock()
    a._send_order=AsyncMock(return_value=dict(id='test',status='closed' if filled else 'open',
                                            filled=1. if filled else .5,average=100.75))
    a.refresh=AsyncMock()
    success=asyncio.run(a.close_position('CAP/USDT',100.75,'Channel Swing PROFIT_LOCK_T2',is_manual=True))
    assert success is filled
    if filled:
        assert a.trades[0]['last_profit_exit_peak_price']==101.
        data=__import__('json').load(open(tmp_path/'testnet.json'))
        assert data['trades'][0]['last_profit_exit_side']=='LONG'
    else:
        assert 'CAP/USDT' in a.positions
        assert not a.trades
